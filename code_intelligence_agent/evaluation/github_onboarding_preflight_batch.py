from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_intelligence_agent.evaluation.github_benchmark_onboarding import (
    parse_github_repo_spec,
)
from code_intelligence_agent.evaluation.github_discovery_fetcher import GitHubAPIError
from code_intelligence_agent.evaluation.github_onboarding_preflight import (
    GitHubOnboardingPreflightReport,
    preflight_from_discovery,
    preflight_search,
    preflight_tree,
)


@dataclass(frozen=True)
class GitHubOnboardingPreflightBatchRun:
    name: str
    mode: str
    status: str
    ready_for_smoke: bool
    output_dir: str
    report_path: str
    recommended_run: dict[str, Any] | None = None
    error: str | None = None
    generated_candidate_count: int = 0
    imported_source_count: int = 0
    issue_codes: list[str] | None = None
    profile_doctor_status: str = ""
    profile_doctor_blocker: str = ""
    profile_doctor_next_action: str = ""
    recommended_test_command: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GitHubOnboardingPreflightBatchReport:
    manifest_path: str
    output_dir: str
    suite_name: str
    passed: bool
    summary: dict[str, Any]
    runs: list[GitHubOnboardingPreflightBatchRun]
    smoke_manifest: dict[str, Any]
    output_paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_path": self.manifest_path,
            "output_dir": self.output_dir,
            "suite_name": self.suite_name,
            "passed": self.passed,
            "summary": self.summary,
            "runs": [run.to_dict() for run in self.runs],
            "smoke_manifest": self.smoke_manifest,
            "output_paths": self.output_paths,
        }


def run_github_onboarding_preflight_batch(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    opener=None,
) -> GitHubOnboardingPreflightBatchReport:
    manifest = Path(manifest_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    suite_name = str(payload.get("suite_name") or payload.get("name") or manifest.stem)
    defaults = _dict(payload.get("defaults"))
    batch_thresholds = _dict(payload.get("thresholds"))
    batch_runs: list[GitHubOnboardingPreflightBatchRun] = []
    smoke_runs: list[dict[str, Any]] = []

    for index, run_value in enumerate(_manifest_runs(payload)):
        run = _dict(run_value)
        options = {**defaults, **run}
        name = _run_name(options, index)
        mode = _run_mode(options)
        run_output = _run_output_dir(options, output_root=output_root, name=name)
        try:
            preflight = _execute_preflight(
                options,
                mode=mode,
                name=name,
                output_dir=run_output,
                manifest_dir=manifest.parent,
                opener=opener,
            )
            recommended_run = _relativize_recommended_run(
                _prefer_saved_discovery_for_smoke(preflight.recommended_run, preflight),
                base_dir=output_root,
            )
            issue_codes = [
                str(_dict(issue).get("code", ""))
                for issue in preflight.issues
                if _dict(issue).get("code")
            ]
            ready_for_smoke = _ready_for_smoke(preflight, options)
            if ready_for_smoke and not preflight.ready_for_smoke:
                issue_codes.append("repository_test_smoke_fallback")
            batch_run = GitHubOnboardingPreflightBatchRun(
                name=name,
                mode=mode,
                status=preflight.status,
                ready_for_smoke=ready_for_smoke,
                output_dir=str(run_output),
                report_path=preflight.output_paths["preflight_json"],
                recommended_run=recommended_run,
                generated_candidate_count=preflight.generated_candidate_count,
                imported_source_count=preflight.imported_source_count,
                issue_codes=issue_codes,
                profile_doctor_status=str(
                    preflight.repository_profile.get("doctor_status") or ""
                ),
                profile_doctor_blocker=str(
                    preflight.repository_profile.get("doctor_blocker") or ""
                ),
                profile_doctor_next_action=str(
                    preflight.repository_profile.get("doctor_next_action") or ""
                ),
                recommended_test_command=str(
                    preflight.repository_profile.get("recommended_test_command")
                    or ""
                ),
            )
            if ready_for_smoke:
                smoke_runs.append(recommended_run)
        except Exception as exc:  # pragma: no cover - CLI boundary tested via output
            batch_run = GitHubOnboardingPreflightBatchRun(
                name=name,
                mode=mode,
                status="error",
                ready_for_smoke=False,
                output_dir=str(run_output),
                report_path="",
                error=f"{type(exc).__name__}: {exc}",
                issue_codes=["preflight_exception"],
                profile_doctor_status="error",
                profile_doctor_blocker="preflight_exception",
                profile_doctor_next_action="Inspect the preflight exception before rerunning.",
            )
        batch_runs.append(batch_run)

    smoke_manifest = _build_smoke_manifest(
        suite_name=suite_name,
        source_payload=payload,
        batch_thresholds=batch_thresholds,
        smoke_runs=smoke_runs,
    )
    offline_manifest = _build_offline_preflight_manifest(
        suite_name=suite_name,
        source_payload=payload,
        batch_runs=batch_runs,
    )
    output_paths = {
        "batch_json": str(output_root / "preflight_batch_report.json"),
        "batch_markdown": str(output_root / "preflight_batch_report.md"),
        "smoke_manifest": str(output_root / "preflight_batch_smoke_manifest.json"),
        "offline_preflight_manifest": str(
            output_root / "preflight_batch_offline_manifest.json"
        ),
        "skipped_json": str(output_root / "preflight_batch_skipped.json"),
        "skipped_markdown": str(output_root / "preflight_batch_skipped.md"),
    }
    summary = _batch_summary(batch_runs)
    report = GitHubOnboardingPreflightBatchReport(
        manifest_path=str(manifest),
        output_dir=str(output_root),
        suite_name=suite_name,
        passed=summary["error_count"] == 0 and summary["ready_count"] > 0,
        summary=summary,
        runs=batch_runs,
        smoke_manifest=smoke_manifest,
        output_paths=output_paths,
    )
    _write_batch_artifacts(report)
    return report


def render_github_onboarding_preflight_batch_markdown(
    report: GitHubOnboardingPreflightBatchReport,
) -> str:
    summary = report.summary
    lines = [
        "# GitHub Onboarding Preflight Batch",
        "",
        f"- Manifest: `{report.manifest_path}`",
        f"- Output Dir: `{report.output_dir}`",
        f"- Suite: `{report.suite_name}`",
        f"- Result: {'PASS' if report.passed else 'FAIL'}",
        f"- Runs: {_int(summary.get('run_count', 0))}",
        f"- Ready: {_int(summary.get('ready_count', 0))}",
        f"- Warnings: {_int(summary.get('warning_count', 0))}",
        f"- Failed: {_int(summary.get('fail_count', 0))}",
        f"- Errors: {_int(summary.get('error_count', 0))}",
        f"- Readiness Rate: {_float(summary.get('readiness_rate', 0.0)):.4f}",
        f"- Top Issue: `{_markdown_cell(summary.get('top_issue_code', ''))}`",
        (
            "- Repository Doctor Statuses: "
            f"{_markdown_cell(_format_counts(_dict(summary.get('profile_doctor_status_counts'))))}"
        ),
        (
            "- Repository Doctor Top Blocker: "
            f"`{_markdown_cell(summary.get('top_profile_doctor_blocker', ''))}`"
        ),
        f"- Generated Candidates: {_int(summary.get('generated_candidates', 0))}",
        f"- Imported Sources: {_int(summary.get('imported_sources', 0))}",
        f"- Smoke Manifest: `{report.output_paths.get('smoke_manifest', '')}`",
        (
            "- Offline Preflight Manifest: "
            f"`{report.output_paths.get('offline_preflight_manifest', '')}`"
        ),
        "",
        "## Readiness Summary",
        "",
        (
            "- Ready Runs: "
            f"{_markdown_cell(_format_name_list(summary.get('ready_run_names')))}"
        ),
        (
            "- Skipped Runs: "
            f"{_markdown_cell(_format_name_list(summary.get('skipped_run_names')))}"
        ),
        (
            "- Error Runs: "
            f"{_markdown_cell(_format_name_list(summary.get('error_run_names')))}"
        ),
        "",
        "## Runs",
        "",
        "| Name | Mode | Status | Ready | Doctor | Doctor Blocker | Test Command | Candidates | Imported | Issues | Report |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for run in report.runs:
        lines.append(
            "| "
            f"{_markdown_cell(run.name)} | "
            f"{_markdown_cell(run.mode)} | "
            f"{_markdown_cell(run.status)} | "
            f"{str(run.ready_for_smoke).lower()} | "
            f"{_markdown_cell(run.profile_doctor_status or 'unknown')} | "
            f"{_markdown_cell(run.profile_doctor_blocker or 'none')} | "
            f"`{_markdown_cell(run.recommended_test_command or 'none')}` | "
            f"{run.generated_candidate_count} | "
            f"{run.imported_source_count} | "
            f"{_markdown_cell(', '.join(run.issue_codes or []))} | "
            f"{_markdown_cell(run.report_path)} |"
        )
    if not report.runs:
        lines.append("| none |  | fail | false | 0 | 0 | no runs |  |")
    lines.extend(
        [
            "",
            "## Smoke Manifest",
            "",
            f"- Ready Runs: {len(_list(report.smoke_manifest.get('runs')))}",
            f"- Path: `{report.output_paths.get('smoke_manifest', '')}`",
            "",
            "```bash",
            (
                "python -m code_intelligence_agent.evaluation.github_onboarding_smoke_runner "
                f"{report.output_paths.get('smoke_manifest', '')} "
                f"{Path(report.output_dir) / 'smoke_batch'} "
                f"--output-json {Path(report.output_dir) / 'smoke_batch' / 'runner.json'} "
                f"--output-markdown {Path(report.output_dir) / 'smoke_batch' / 'runner.md'}"
            ),
            "```",
            "",
            "## Offline Preflight Manifest",
            "",
            (
                "- Path: "
                f"`{report.output_paths.get('offline_preflight_manifest', '')}`"
            ),
            "",
            "```bash",
            (
                "python -m code_intelligence_agent.evaluation.github_onboarding_pipeline "
                f"{report.output_paths.get('offline_preflight_manifest', '')} "
                f"{Path(report.output_dir) / 'offline_pipeline'}"
            ),
            "```",
        ]
    )
    return "\n".join(lines)


def render_github_onboarding_preflight_skipped_markdown(
    report: GitHubOnboardingPreflightBatchReport,
) -> str:
    lines = [
        "# GitHub Onboarding Preflight Skipped Runs",
        "",
        "| Name | Mode | Status | Error | Issues |",
        "| --- | --- | --- | --- | --- |",
    ]
    skipped = [run for run in report.runs if not run.ready_for_smoke]
    for run in skipped:
        lines.append(
            "| "
            f"{_markdown_cell(run.name)} | "
            f"{_markdown_cell(run.mode)} | "
            f"{_markdown_cell(run.status)} | "
            f"{_markdown_cell(run.error or '')} | "
            f"{_markdown_cell(', '.join(run.issue_codes or []))} |"
        )
    if not skipped:
        lines.append("| none |  | pass |  |  |")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run preflight over a repo/tree/search/from-discovery manifest and "
            "emit a smoke-runner manifest for ready repositories."
        )
    )
    parser.add_argument("manifest", help="Path to preflight batch manifest.")
    parser.add_argument("output_dir", help="Directory for batch artifacts.")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    return parser


def main(argv: list[str] | None = None, opener=None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    report = run_github_onboarding_preflight_batch(
        args.manifest,
        args.output_dir,
        opener=opener,
    )
    json_payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    markdown = render_github_onboarding_preflight_batch_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_payload, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown, encoding="utf-8")
    if args.format == "json":
        print(json_payload)
    else:
        print(markdown)
    raise SystemExit(0 if report.passed else 1)


def _execute_preflight(
    options: dict[str, Any],
    *,
    mode: str,
    name: str,
    output_dir: Path,
    manifest_dir: Path,
    opener: Any,
) -> GitHubOnboardingPreflightReport:
    shared = _shared_preflight_kwargs(options, run_name=name)
    if mode == "from-discovery":
        discovery = options.get("discovery") or options.get("discovery_path")
        if not discovery:
            raise ValueError("from-discovery preflight requires discovery")
        discovery_path = _resolve_path(str(discovery), base_dir=manifest_dir)
        report = preflight_from_discovery(
            json.loads(discovery_path.read_text(encoding="utf-8")),
            output_dir,
            source=str(discovery_path),
            mode="from-discovery",
            owner=_optional_str(options.get("owner")),
            repo=_optional_str(options.get("repo")),
            ref=_optional_str(options.get("ref")),
            original_run={
                "mode": "from-discovery",
                "discovery": str(discovery_path),
                "owner": options.get("owner"),
                "repo": options.get("repo"),
                "ref": options.get("ref"),
                "thresholds": options.get("thresholds"),
                "fallback": options.get("fallback"),
                "auto_fallback": options.get("auto_fallback"),
                "auto_remediate_benchmark": options.get(
                    "auto_remediate_benchmark"
                ),
            },
            **shared,
        )
        return _with_smoke_run_options(report, options)
    if mode == "repo":
        repo_spec = options.get("repo_spec") or options.get("repo")
        if not repo_spec:
            raise ValueError("repo preflight requires repo or repo_spec")
        owner, repo = parse_github_repo_spec(str(repo_spec))
        report = preflight_tree(
            owner,
            repo,
            output_dir,
            ref=_optional_str(options.get("ref")),
            token=_token_from_env(_optional_str(options.get("token_env"))),
            recursive=not _bool(options.get("no_recursive")),
            api_base_url=str(options.get("api_base_url") or "https://api.github.com"),
            timeout=_int(options.get("timeout")) or 20,
            opener=opener,
            **shared,
        )
        return _with_repo_recommended_run(report, str(repo_spec), options=options)
    if mode == "tree":
        owner = options.get("owner")
        repo = options.get("repo")
        if not owner or not repo:
            raise ValueError("tree preflight requires owner and repo")
        report = preflight_tree(
            str(owner),
            str(repo),
            output_dir,
            ref=_optional_str(options.get("ref")),
            token=_token_from_env(_optional_str(options.get("token_env"))),
            recursive=not _bool(options.get("no_recursive")),
            api_base_url=str(options.get("api_base_url") or "https://api.github.com"),
            timeout=_int(options.get("timeout")) or 20,
            opener=opener,
            **shared,
        )
        return _with_smoke_run_options(report, options)
    if mode == "search":
        query = options.get("query")
        if not query:
            raise ValueError("search preflight requires query")
        report = preflight_search(
            str(query),
            output_dir,
            owner=_optional_str(options.get("owner")),
            repo=_optional_str(options.get("repo")),
            ref=_optional_str(options.get("ref")),
            token=_token_from_env(_optional_str(options.get("token_env"))),
            extension=_optional_str(options.get("extension")) or "py",
            per_page=_int(options.get("per_page")) or 100,
            max_pages=_int(options.get("max_pages")) or 1,
            api_base_url=str(options.get("api_base_url") or "https://api.github.com"),
            timeout=_int(options.get("timeout")) or 20,
            opener=opener,
            **shared,
        )
        return _with_smoke_run_options(report, options)
    raise ValueError(f"Unsupported preflight mode: {mode}")


def _shared_preflight_kwargs(options: dict[str, Any], *, run_name: str) -> dict[str, Any]:
    return {
        "include": _string_list_or_none(options.get("include")),
        "exclude": _string_list_or_none(options.get("exclude")),
        "preserve_paths": _bool(options.get("preserve_paths")),
        "target_prefix": str(options.get("target_prefix") or ""),
        "recipes": _string_list_or_none(
            options.get("recipe", options.get("recipes"))
        ),
        "source_cache_dir": options.get("source_cache_dir"),
        "sample_sources": _int(options.get("sample_sources")) or 20,
        "max_candidates": _int(options.get("max_candidates")) or 10,
        "max_auto_recipes": _int(options.get("max_auto_recipes")) or 3,
        "preset": str(options.get("preset") or "smoke"),
        "run_name": run_name,
        "auto_scoped_include": _bool(options.get("auto_scoped_include")),
    }


def _with_repo_recommended_run(
    report: GitHubOnboardingPreflightReport,
    repo_spec: str,
    *,
    options: dict[str, Any],
) -> GitHubOnboardingPreflightReport:
    run = dict(report.recommended_run)
    run["mode"] = "repo"
    run["repo"] = repo_spec
    run.pop("owner", None)
    run = _copy_smoke_run_options(run, options)
    manifest = dict(report.recommended_manifest)
    manifest["runs"] = [run]
    return GitHubOnboardingPreflightReport(
        **{
            **report.to_dict(),
            "mode": "repo",
            "recommended_run": run,
            "recommended_manifest": manifest,
        }
    )


def _with_smoke_run_options(
    report: GitHubOnboardingPreflightReport,
    options: dict[str, Any],
) -> GitHubOnboardingPreflightReport:
    run = _copy_smoke_run_options(dict(report.recommended_run), options)
    manifest = dict(report.recommended_manifest)
    manifest["runs"] = [run]
    return GitHubOnboardingPreflightReport(
        **{
            **report.to_dict(),
            "recommended_run": run,
            "recommended_manifest": manifest,
        }
    )


def _copy_smoke_run_options(
    run: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    passthrough_keys = (
        "thresholds",
        "fallback",
        "auto_fallback",
        "auto_remediate_benchmark",
        "repository_test_root",
        "repository_test_timeout",
        "repository_test_failure_overlay_candidate_limit",
        "repository_test_reflection_mode",
        "repository_test_reflection_rounds",
        "repository_test_reflection_width",
        "run_repository_test_environment_setup",
        "run_repository_test_retry",
        "run_repository_test_retry_prerequisites",
        "auto_repository_test_retry",
        "auto_repository_test_retry_max_risk",
        "auto_repository_test_retry_allowed_runners",
        "repository_test_environment_setup_timeout",
        "checkout_repository_tests",
        "repository_checkout_timeout",
        "repository_checkout_depth",
        "no_repository_test_command",
        "auto_scoped_include",
        "source_cache_dir",
        "dependency_max_depth",
    )
    for key in passthrough_keys:
        if key in options and options[key] is not None:
            run[key] = options[key]
    return run


def _ready_for_smoke(
    preflight: GitHubOnboardingPreflightReport,
    options: dict[str, Any],
) -> bool:
    if preflight.ready_for_smoke:
        return True
    if preflight.status == "fail":
        return False
    if _bool(options.get("no_repository_test_command")):
        return False
    repository_test_enabled = bool(options.get("repository_test_root")) or _bool(
        options.get("checkout_repository_tests")
    )
    return repository_test_enabled and preflight.imported_source_count > 0


def _build_smoke_manifest(
    *,
    suite_name: str,
    source_payload: dict[str, Any],
    batch_thresholds: dict[str, Any],
    smoke_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "suite_name": f"{suite_name}_preflight_smoke",
        "description": (
            "Generated by github_onboarding_preflight_batch from ready preflight runs."
        ),
        "runs": smoke_runs,
    }
    if isinstance(source_payload.get("smoke_defaults"), dict):
        payload["defaults"] = source_payload["smoke_defaults"]
    if batch_thresholds:
        payload["thresholds"] = batch_thresholds
    return payload


def _build_offline_preflight_manifest(
    *,
    suite_name: str,
    source_payload: dict[str, Any],
    batch_runs: list[GitHubOnboardingPreflightBatchRun],
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for batch_run in batch_runs:
        if batch_run.status == "error" or not batch_run.recommended_run:
            continue
        run = dict(batch_run.recommended_run)
        run["name"] = batch_run.name
        if run.get("mode") == "from-discovery" and "sample_sources" not in run:
            max_sources = _int(run.get("max_sources", 0))
            if max_sources > 0:
                run["sample_sources"] = max_sources
        runs.append(run)
    payload = {
        "suite_name": f"{suite_name}_offline_preflight",
        "description": (
            "Generated by github_onboarding_preflight_batch for offline reruns "
            "from saved preflight_discovery.json artifacts."
        ),
        "runs": runs,
    }
    for key in ("thresholds", "promotion_gate", "smoke_defaults"):
        value = source_payload.get(key)
        if isinstance(value, dict) and value:
            payload[key] = value
    return payload


def _batch_summary(runs: list[GitHubOnboardingPreflightBatchRun]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    issue_counts: dict[str, int] = {}
    profile_doctor_status_counts: dict[str, int] = {}
    profile_doctor_blocker_counts: dict[str, int] = {}
    for run in runs:
        status_counts[run.status] = status_counts.get(run.status, 0) + 1
        doctor_status = run.profile_doctor_status or "unknown"
        profile_doctor_status_counts[doctor_status] = (
            profile_doctor_status_counts.get(doctor_status, 0) + 1
        )
        doctor_blocker = run.profile_doctor_blocker or "none"
        profile_doctor_blocker_counts[doctor_blocker] = (
            profile_doctor_blocker_counts.get(doctor_blocker, 0) + 1
        )
        for issue in run.issue_codes or []:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
    ready_count = sum(1 for run in runs if run.ready_for_smoke)
    skipped_runs = [run for run in runs if not run.ready_for_smoke]
    return {
        "run_count": len(runs),
        "ready_count": ready_count,
        "skipped_count": len(skipped_runs),
        "readiness_rate": _ratio(ready_count, len(runs)),
        "ready_run_names": [run.name for run in runs if run.ready_for_smoke],
        "skipped_run_names": [run.name for run in skipped_runs],
        "error_run_names": [run.name for run in runs if run.status == "error"],
        "pass_count": status_counts.get("pass", 0),
        "warning_count": status_counts.get("warning", 0),
        "fail_count": status_counts.get("fail", 0),
        "error_count": status_counts.get("error", 0),
        "generated_candidates": sum(run.generated_candidate_count for run in runs),
        "imported_sources": sum(run.imported_source_count for run in runs),
        "status_counts": dict(sorted(status_counts.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "top_issue_code": _top_key(issue_counts),
        "profile_doctor_status_counts": dict(
            sorted(profile_doctor_status_counts.items())
        ),
        "profile_doctor_blocker_counts": dict(
            sorted(profile_doctor_blocker_counts.items())
        ),
        "top_profile_doctor_blocker": _top_key(
            {
                key: value
                for key, value in profile_doctor_blocker_counts.items()
                if key != "none"
            }
        ),
    }


def _write_batch_artifacts(report: GitHubOnboardingPreflightBatchReport) -> None:
    paths = report.output_paths
    Path(paths["batch_json"]).write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["batch_markdown"]).write_text(
        render_github_onboarding_preflight_batch_markdown(report),
        encoding="utf-8",
    )
    Path(paths["smoke_manifest"]).write_text(
        json.dumps(report.smoke_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    offline_manifest = _build_offline_preflight_manifest(
        suite_name=report.suite_name,
        source_payload=_read_json(report.manifest_path),
        batch_runs=report.runs,
    )
    Path(paths["offline_preflight_manifest"]).write_text(
        json.dumps(offline_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    skipped = [run.to_dict() for run in report.runs if not run.ready_for_smoke]
    Path(paths["skipped_json"]).write_text(
        json.dumps({"runs": skipped}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["skipped_markdown"]).write_text(
        render_github_onboarding_preflight_skipped_markdown(report),
        encoding="utf-8",
    )


def _relativize_recommended_run(
    run: dict[str, Any],
    *,
    base_dir: Path,
) -> dict[str, Any]:
    updated = dict(run)
    discovery = updated.get("discovery")
    if discovery:
        path = Path(str(discovery))
        try:
            updated["discovery"] = str(path.relative_to(base_dir))
        except ValueError:
            updated["discovery"] = str(path)
    return updated


def _prefer_saved_discovery_for_smoke(
    run: dict[str, Any],
    preflight: GitHubOnboardingPreflightReport,
) -> dict[str, Any]:
    discovery_path = str(preflight.output_paths.get("discovery") or "").strip()
    if not discovery_path:
        return dict(run)
    updated = dict(run)
    updated["mode"] = "from-discovery"
    updated["discovery"] = discovery_path
    if "repo_spec" in updated and "repo" not in updated:
        updated["repo"] = updated["repo_spec"]
    if updated.get("repo") and not updated.get("owner"):
        try:
            owner, repo = parse_github_repo_spec(str(updated["repo"]))
        except ValueError:
            owner, repo = "", ""
        if owner and repo:
            updated["owner"] = owner
            updated["repo"] = repo
    updated.pop("repo_spec", None)
    updated.pop("query", None)
    return updated


def _manifest_runs(payload: dict[str, Any]) -> list[Any]:
    for key in ("runs", "repos", "items"):
        values = payload.get(key)
        if isinstance(values, list):
            return values
    return []


def _run_mode(options: dict[str, Any]) -> str:
    if options.get("mode"):
        return str(options["mode"])
    if options.get("discovery") or options.get("discovery_path"):
        return "from-discovery"
    if options.get("query"):
        return "search"
    if options.get("owner") and options.get("repo") and options.get("ref"):
        return "tree"
    return "repo"


def _run_name(options: dict[str, Any], index: int) -> str:
    if options.get("name"):
        return str(options["name"])
    if options.get("repo_spec"):
        return _slug(str(options["repo_spec"]))
    if options.get("repo") and not options.get("owner"):
        return _slug(str(options["repo"]))
    if options.get("owner") and options.get("repo"):
        return _slug(f"{options['owner']}_{options['repo']}")
    if options.get("query"):
        return _slug(str(options["query"]))
    return f"run_{index + 1}"


def _run_output_dir(
    options: dict[str, Any],
    *,
    output_root: Path,
    name: str,
) -> Path:
    raw = options.get("preflight_output_dir") or options.get("output_dir")
    if raw:
        path = Path(str(raw))
        if path.is_absolute():
            return path
        return output_root / path
    return output_root / "preflight_runs" / _slug(name)


def _resolve_path(value: str, *, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def _read_json(path: str | Path) -> dict[str, Any]:
    return _dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _token_from_env(env_name: str | None) -> str | None:
    if not env_name:
        return None
    return os.environ.get(env_name)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _string_list_or_none(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


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


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _top_key(counts: dict[str, int]) -> str:
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _format_name_list(value: Any) -> str:
    names = [str(item) for item in _list(value)]
    return ", ".join(names) if names else "none"


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={_int(value)}" for key, value in sorted(counts.items())
    )


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value]
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "run"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


if __name__ == "__main__":
    main()
