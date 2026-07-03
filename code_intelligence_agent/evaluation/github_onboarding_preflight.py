from __future__ import annotations

import argparse
import ast
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.agents.bug_detector import RuleBasedBugDetector
from code_intelligence_agent.core.ast_analyzer import ASTAnalyzer
from code_intelligence_agent.evaluation.benchmark_source_miner import (
    mine_recipe_sources,
    render_source_mining_markdown,
)
from code_intelligence_agent.evaluation.github_benchmark_onboarding import (
    DEFAULT_AUTO_RECIPE_LIMIT,
    build_onboarding_recipe_selection,
    parse_github_repo_spec,
    parse_github_repo_spec_with_ref,
    render_onboarding_recipe_selection_markdown,
    _read_source_text_for_selection as read_source_text_for_selection,
    _source_recipe_score as benchmark_source_recipe_score,
)
from code_intelligence_agent.evaluation.github_discovery_fetcher import (
    GitHubAPIError,
    fetch_code_search_discovery,
    fetch_tree_discovery,
)
from code_intelligence_agent.evaluation.github_repository_profile import (
    build_github_repository_profile,
    render_github_repository_profile_markdown,
)
from code_intelligence_agent.evaluation.github_source_importer import (
    import_github_sources,
    render_github_source_import_markdown,
)
from code_intelligence_agent.evaluation.repository_test_failure_overlay import (
    OVERLAY_RULE_TRIGGER_PRIORS,
    SUPPORTED_OVERLAY_RULES,
)


DEFAULT_PREFLIGHT_SAMPLE_SOURCES = 20
DEFAULT_PREFLIGHT_MAX_CANDIDATES = 10
DEFAULT_REPAIR_SCORE_POOL = 50
DEFAULT_CANDIDATE_RECOVERY_POOL = 200
AUXILIARY_SOURCE_ROOTS = {
    "benchmark",
    "benchmarks",
    "doc",
    "docs",
    "example",
    "examples",
    "sample",
    "samples",
    "script",
    "scripts",
    "test",
    "tests",
    "tool",
    "tools",
}
GENERATED_RULE_QUALITY_BONUS = {
    "inplace_api_return_value": 280,
    "mutable_default_arg": 220,
    "dict_missing_key_guard": 180,
    "stringified_numeric_value": 170,
    "enumerate_start_zero_counter": 150,
    "possible_index_overrun": 140,
    "iterator_double_consumption": 130,
    "missing_len_zero_guard": 120,
    "always_true_len_check": 80,
    "inverted_empty_guard": 70,
    "identity_comparison_literal": 60,
}
REPAIR_SOURCE_PATH_HINT_WEIGHTS = {
    "cipher": 16,
    "crypt": 16,
    "decrypt": 16,
    "encrypt": 16,
    "parser": 14,
    "validator": 14,
    "normalizer": 14,
    "index": 12,
    "iterator": 12,
    "counter": 12,
    "lookup": 12,
    "dict": 12,
    "mapping": 12,
    "cache": 12,
    "window": 12,
    "token": 10,
    "score": 10,
    "format": 10,
    "sort": 10,
    "search": 10,
    "average": 5,
    "mean": 5,
    "median": 5,
    "mode": 5,
}


@dataclass(frozen=True)
class GitHubOnboardingPreflightReport:
    mode: str
    source: str
    output_dir: str
    status: str
    ready_for_smoke: bool
    discovery_item_count: int
    imported_source_count: int
    skipped_source_count: int
    sampled_source_count: int
    generated_candidate_count: int
    recommended_run: dict[str, Any]
    recommended_manifest: dict[str, Any]
    recommended_commands: list[str]
    repository_profile: dict[str, Any]
    recipe_selection: dict[str, Any]
    mining_summary: dict[str, Any]
    issues: list[dict[str, Any]]
    next_actions: list[str]
    output_paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def preflight_from_discovery(
    discovery_payload: dict[str, Any],
    output_dir: str | Path,
    *,
    source: str = "discovery",
    mode: str = "from-discovery",
    owner: str | None = None,
    repo: str | None = None,
    ref: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    preserve_paths: bool = False,
    target_prefix: str = "",
    recipes: list[str] | None = None,
    source_cache_dir: str | Path | None = None,
    sample_sources: int = DEFAULT_PREFLIGHT_SAMPLE_SOURCES,
    max_candidates: int = DEFAULT_PREFLIGHT_MAX_CANDIDATES,
    max_auto_recipes: int = DEFAULT_AUTO_RECIPE_LIMIT,
    preset: str = "smoke",
    run_name: str | None = None,
    original_run: dict[str, Any] | None = None,
    auto_scoped_include: bool = False,
) -> GitHubOnboardingPreflightReport:
    if sample_sources <= 0:
        raise ValueError("sample_sources must be positive")
    if max_candidates <= 0:
        raise ValueError("max_candidates must be positive")
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root = Path(source_cache_dir) if source_cache_dir else output_root / "source_cache"

    discovery_path = output_root / "preflight_discovery.json"
    discovery_path.write_text(
        json.dumps(discovery_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    import_report = import_github_sources(
        discovery_payload,
        source_path=source,
        owner=owner,
        repo=repo,
        ref=ref,
        include=include,
        exclude=exclude,
        preserve_paths=preserve_paths,
        target_prefix=target_prefix,
    )
    imported_sources = list(import_report.source_entries)
    sampling_recipes = _sampling_recipes(
        imported_sources,
        requested_recipes=recipes,
        source_cache_dir=cache_root,
        max_auto_recipes=max_auto_recipes,
        enabled=auto_scoped_include,
    )
    sampled_sources = _sample_sources(
        imported_sources,
        limit=sample_sources,
        recipes=sampling_recipes,
        source_cache_dir=cache_root if auto_scoped_include else None,
    )
    sampled_payload = {"sources": sampled_sources}
    recipe_selection = build_onboarding_recipe_selection(
        sampled_payload,
        requested_recipes=recipes,
        source_cache_dir=cache_root,
        max_auto_recipes=max_auto_recipes,
    )
    selected_recipes = _string_list(recipe_selection.get("selected_recipes"))
    mining_report = mine_recipe_sources(
        sampled_payload,
        recipes=selected_recipes,
        source_path=str(output_root / "preflight_sources.json"),
        source_cache_dir=cache_root,
    )
    auto_scoped_include_source = "preflight_sampled_sources"
    recovery = _recover_auto_scoped_candidate_sample(
        imported_sources,
        requested_recipes=recipes,
        source_cache_dir=cache_root,
        sample_sources=sample_sources,
        max_auto_recipes=max_auto_recipes,
        enabled=(
            auto_scoped_include
            and mining_report.generated_count <= 0
        ),
    )
    if recovery is not None:
        (
            sampled_sources,
            selected_recipes,
            recipe_selection,
            mining_report,
        ) = recovery
        sampled_payload = {"sources": sampled_sources}
        auto_scoped_include_source = "preflight_candidate_recovery"

    repository_profile = build_github_repository_profile(
        discovery_payload,
        import_report.to_dict(),
        sampled_sources=sampled_sources,
    )
    mining_payload = mining_report.to_dict()
    mining_summary = _mining_summary(mining_payload)
    issues = _preflight_issues(
        discovery_item_count=_discovery_item_count(discovery_payload),
        imported_source_count=import_report.source_count,
        sampled_source_count=len(sampled_sources),
        generated_candidate_count=mining_report.generated_count,
        repository_profile=repository_profile,
        recipe_selection=recipe_selection,
    )
    status = _status_from_issues(issues)
    ready_for_smoke = status != "fail" and mining_report.generated_count > 0
    effective_run_name = run_name or _default_run_name(
        mode=mode,
        source=source,
        owner=owner,
        repo=repo,
    )
    recommended_run = _recommended_run(
        mode=mode,
        source=source,
        discovery_path=discovery_path,
        name=effective_run_name,
        owner=owner,
        repo=repo,
        ref=ref,
        preset=preset,
        include=include,
        exclude=exclude,
        preserve_paths=preserve_paths,
        target_prefix=target_prefix,
        selected_recipes=selected_recipes,
        sample_sources=sample_sources,
        max_candidates=max_candidates,
        generated_candidate_count=mining_report.generated_count,
        repository_profile=repository_profile,
        original_run=original_run,
        sampled_sources=sampled_sources,
        auto_scoped_include=auto_scoped_include,
        auto_scoped_include_source=auto_scoped_include_source,
    )
    recommended_manifest = {
        "suite_name": f"{_slug(effective_run_name)}_onboarding_smoke",
        "description": "Generated by github_onboarding_preflight.",
        "runs": [recommended_run],
    }
    recommended_commands = _recommended_commands(
        recommended_manifest_path=output_root / "preflight_recommended_manifest.json",
        output_root=output_root,
    )
    next_actions = _next_actions(
        status=status,
        ready_for_smoke=ready_for_smoke,
        generated_candidate_count=mining_report.generated_count,
    )

    output_paths = {
        "discovery": str(discovery_path),
        "source_import_json": str(output_root / "preflight_source_import.json"),
        "source_import_markdown": str(output_root / "preflight_source_import.md"),
        "sources": str(output_root / "preflight_sources.json"),
        "recipe_selection_json": str(output_root / "preflight_recipe_selection.json"),
        "recipe_selection_markdown": str(
            output_root / "preflight_recipe_selection.md"
        ),
        "source_mining_json": str(output_root / "preflight_source_mining.json"),
        "source_mining_markdown": str(output_root / "preflight_source_mining.md"),
        "recommended_manifest": str(
            output_root / "preflight_recommended_manifest.json"
        ),
        "preflight_json": str(output_root / "preflight_report.json"),
        "preflight_markdown": str(output_root / "preflight_report.md"),
        "source_cache_dir": str(cache_root),
    }
    report = GitHubOnboardingPreflightReport(
        mode=mode,
        source=source,
        output_dir=str(output_root),
        status=status,
        ready_for_smoke=ready_for_smoke,
        discovery_item_count=_discovery_item_count(discovery_payload),
        imported_source_count=import_report.source_count,
        skipped_source_count=import_report.skipped_count,
        sampled_source_count=len(sampled_sources),
        generated_candidate_count=mining_report.generated_count,
        recommended_run=recommended_run,
        recommended_manifest=recommended_manifest,
        recommended_commands=recommended_commands,
        repository_profile=repository_profile,
        recipe_selection=recipe_selection,
        mining_summary=mining_summary,
        issues=issues,
        next_actions=next_actions,
        output_paths=output_paths,
    )

    _write_preflight_artifacts(
        report,
        import_report=import_report.to_dict(),
        import_markdown=render_github_source_import_markdown(import_report),
        sampled_payload=sampled_payload,
        recipe_selection=recipe_selection,
        mining_payload=mining_payload,
        mining_markdown=render_source_mining_markdown(mining_report),
    )
    return report


def preflight_tree(
    owner: str,
    repo: str,
    output_dir: str | Path,
    *,
    ref: str | None = None,
    token: str | None = None,
    recursive: bool = True,
    api_base_url: str = "https://api.github.com",
    timeout: int = 20,
    opener=None,
    **kwargs: Any,
) -> GitHubOnboardingPreflightReport:
    discovery = fetch_tree_discovery(
        owner=owner,
        repo=repo,
        ref=ref,
        token=token,
        recursive=recursive,
        api_base_url=api_base_url,
        timeout=timeout,
        opener=opener,
    )
    resolved_ref = str(discovery.discovery_payload.get("ref") or ref or "")
    return preflight_from_discovery(
        discovery.discovery_payload,
        output_dir,
        source=f"github-tree:{owner}/{repo}@{resolved_ref}",
        mode="tree",
        owner=owner,
        repo=repo,
        ref=resolved_ref,
        original_run={
            "mode": "tree",
            "owner": owner,
            "repo": repo,
            "ref": resolved_ref,
            "no_recursive": not recursive,
        },
        **kwargs,
    )


def preflight_search(
    query: str,
    output_dir: str | Path,
    *,
    owner: str | None = None,
    repo: str | None = None,
    ref: str | None = None,
    token: str | None = None,
    extension: str | None = "py",
    per_page: int = 100,
    max_pages: int = 1,
    api_base_url: str = "https://api.github.com",
    timeout: int = 20,
    opener=None,
    **kwargs: Any,
) -> GitHubOnboardingPreflightReport:
    discovery = fetch_code_search_discovery(
        query=query,
        owner=owner,
        repo=repo,
        ref=ref,
        token=token,
        extension=extension,
        per_page=per_page,
        max_pages=max_pages,
        api_base_url=api_base_url,
        timeout=timeout,
        opener=opener,
    )
    scope = f" repo={owner}/{repo}" if owner and repo else ""
    return preflight_from_discovery(
        discovery.discovery_payload,
        output_dir,
        source=f"github-search:{query}{scope}",
        mode="search",
        owner=owner,
        repo=repo,
        ref=ref,
        original_run={
            "mode": "search",
            "query": query,
            "owner": owner,
            "repo": repo,
            "ref": ref,
            "extension": extension,
            "per_page": per_page,
            "max_pages": max_pages,
        },
        **kwargs,
    )


def render_github_onboarding_preflight_markdown(
    report: GitHubOnboardingPreflightReport,
) -> str:
    profile = report.repository_profile
    mining = report.mining_summary
    lines = [
        "# GitHub Onboarding Preflight",
        "",
        f"- Source: `{report.source}`",
        f"- Mode: `{report.mode}`",
        f"- Status: `{report.status}`",
        f"- Ready For Smoke: {str(report.ready_for_smoke).lower()}",
        f"- Discovery Items: {report.discovery_item_count}",
        f"- Imported Sources: {report.imported_source_count}",
        f"- Skipped Sources: {report.skipped_source_count}",
        f"- Sampled Sources: {report.sampled_source_count}",
        f"- Generated Candidates: {report.generated_candidate_count}",
        f"- Python Source Ratio: {_float(profile.get('python_source_ratio', 0.0)):.4f}",
        (
            "- Selected Recipes: "
            f"{_markdown_cell(', '.join(_string_list(report.recommended_run.get('recipe'))))}"
        ),
        (
            "- Recommended Test Command: "
            f"`{_markdown_cell(profile.get('recommended_test_command') or 'none')}`"
        ),
        (
            "- Recommended Target Prefix: "
            f"`{_markdown_cell(profile.get('recommended_target_prefix') or 'none')}`"
        ),
        (
            "- Repository Doctor: "
            f"status={_markdown_cell(profile.get('doctor_status') or 'unknown')}; "
            f"blocker={_markdown_cell(profile.get('doctor_blocker') or 'none')}; "
            f"score={_float(profile.get('doctor_score', 0.0)):.2f}"
        ),
        (
            "- Repository Doctor Next Action: "
            f"{_markdown_cell(profile.get('doctor_next_action') or 'none')}"
        ),
        (
            "- Rule Coverage: "
            f"{_markdown_cell(_format_counts(_dict(mining.get('rule_counts'))))}"
        ),
        (
            "- Bug Type Coverage: "
            f"{_markdown_cell(_format_counts(_dict(mining.get('bug_type_counts'))))}"
        ),
        "",
        "## Issues",
        "",
    ]
    if report.issues:
        for issue in report.issues:
            lines.append(
                "- "
                f"`{_markdown_cell(issue.get('code', ''))}` "
                f"{_markdown_cell(issue.get('message', ''))}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    lines.extend(f"- {action}" for action in report.next_actions)
    lines.extend(
        [
            "",
            "## Recommended Runner Manifest Entry",
            "",
            "```json",
            json.dumps(report.recommended_run, indent=2, ensure_ascii=False),
            "```",
            "",
            "## Commands",
            "",
            "```bash",
            "\n".join(report.recommended_commands),
            "```",
            "",
            "## Top Directories",
            "",
            "| Directory | Sources |",
            "| --- | ---: |",
        ]
    )
    for directory, count in _dict(profile.get("top_source_directories")).items():
        lines.append(f"| {_markdown_cell(directory)} | {_int(count)} |")
    if not _dict(profile.get("top_source_directories")):
        lines.append("| none | 0 |")
    lines.extend(["", "## Repository Profile", ""])
    lines.append(render_github_repository_profile_markdown(profile))
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight a GitHub discovery/repository before running benchmark "
            "onboarding. Outputs repository fit, recipe hints and a runner entry."
        )
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    from_discovery = subparsers.add_parser(
        "from-discovery",
        help="Use an existing discovery JSON payload.",
    )
    from_discovery.add_argument("discovery")
    from_discovery.add_argument("output_dir")
    from_discovery.add_argument("--owner")
    from_discovery.add_argument("--repo")
    from_discovery.add_argument("--ref")
    _add_shared_args(from_discovery)

    tree = subparsers.add_parser("tree", help="Fetch a GitHub tree and preflight it.")
    tree.add_argument("owner")
    tree.add_argument("repo")
    tree.add_argument("output_dir")
    tree.add_argument("--ref")
    tree.add_argument("--no-recursive", action="store_true")
    _add_network_args(tree)
    _add_shared_args(tree)

    repo_parser = subparsers.add_parser(
        "repo",
        help="Fetch a GitHub repo by owner/repo or URL and preflight its tree.",
    )
    repo_parser.add_argument("repo_spec")
    repo_parser.add_argument("output_dir")
    repo_parser.add_argument("--ref")
    repo_parser.add_argument("--no-recursive", action="store_true")
    _add_network_args(repo_parser)
    _add_shared_args(repo_parser)

    search = subparsers.add_parser(
        "search",
        help="Fetch GitHub code search results and preflight them.",
    )
    search.add_argument("query")
    search.add_argument("output_dir")
    search.add_argument("--owner")
    search.add_argument("--repo")
    search.add_argument("--ref")
    search.add_argument("--extension", default="py")
    search.add_argument("--per-page", type=int, default=100)
    search.add_argument("--max-pages", type=int, default=1)
    _add_network_args(search)
    _add_shared_args(search)
    return parser


def main(argv: list[str] | None = None, opener=None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    shared = _shared_kwargs(args)
    try:
        if args.mode == "from-discovery":
            discovery_path = Path(args.discovery)
            report = preflight_from_discovery(
                json.loads(discovery_path.read_text(encoding="utf-8")),
                args.output_dir,
                source=str(discovery_path),
                mode="from-discovery",
                owner=args.owner,
                repo=args.repo,
                ref=args.ref,
                original_run={
                    "mode": "from-discovery",
                    "discovery": str(discovery_path),
                    "owner": args.owner,
                    "repo": args.repo,
                    "ref": args.ref,
                },
                **shared,
            )
        elif args.mode == "repo":
            owner, repo, inferred_ref = parse_github_repo_spec_with_ref(
                args.repo_spec
            )
            report = preflight_tree(
                owner,
                repo,
                args.output_dir,
                ref=args.ref or inferred_ref,
                token=_token_from_env(args.token_env),
                recursive=not args.no_recursive,
                api_base_url=args.api_base_url,
                timeout=args.timeout,
                opener=opener,
                **shared,
            )
            report = _replace_recommended_repo_run(report, args.repo_spec)
        elif args.mode == "tree":
            report = preflight_tree(
                args.owner,
                args.repo,
                args.output_dir,
                ref=args.ref,
                token=_token_from_env(args.token_env),
                recursive=not args.no_recursive,
                api_base_url=args.api_base_url,
                timeout=args.timeout,
                opener=opener,
                **shared,
            )
        elif args.mode == "search":
            report = preflight_search(
                args.query,
                args.output_dir,
                owner=args.owner,
                repo=args.repo,
                ref=args.ref,
                token=_token_from_env(args.token_env),
                extension=args.extension,
                per_page=args.per_page,
                max_pages=args.max_pages,
                api_base_url=args.api_base_url,
                timeout=args.timeout,
                opener=opener,
                **shared,
            )
        else:  # pragma: no cover - argparse enforces choices
            parser.error(f"Unsupported mode: {args.mode}")
    except GitHubAPIError as exc:
        parser.exit(1, f"error: {exc}\n")

    json_payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
    markdown = render_github_onboarding_preflight_markdown(report)
    if args.output_json:
        Path(args.output_json).write_text(json_payload, encoding="utf-8")
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown, encoding="utf-8")
    if args.format == "json":
        print(json_payload)
    else:
        print(markdown)
    raise SystemExit(0 if report.status != "fail" else 1)


def _write_preflight_artifacts(
    report: GitHubOnboardingPreflightReport,
    *,
    import_report: dict[str, Any],
    import_markdown: str,
    sampled_payload: dict[str, Any],
    recipe_selection: dict[str, Any],
    mining_payload: dict[str, Any],
    mining_markdown: str,
) -> None:
    paths = report.output_paths
    Path(paths["source_import_json"]).write_text(
        json.dumps(import_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["source_import_markdown"]).write_text(
        import_markdown,
        encoding="utf-8",
    )
    Path(paths["sources"]).write_text(
        json.dumps(sampled_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["recipe_selection_json"]).write_text(
        json.dumps(recipe_selection, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["recipe_selection_markdown"]).write_text(
        render_onboarding_recipe_selection_markdown(recipe_selection),
        encoding="utf-8",
    )
    Path(paths["source_mining_json"]).write_text(
        json.dumps(mining_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["source_mining_markdown"]).write_text(
        mining_markdown,
        encoding="utf-8",
    )
    Path(paths["recommended_manifest"]).write_text(
        json.dumps(report.recommended_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["preflight_json"]).write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["preflight_markdown"]).write_text(
        render_github_onboarding_preflight_markdown(report),
        encoding="utf-8",
    )


def _sample_sources(
    sources: list[dict[str, Any]],
    *,
    limit: int,
    recipes: list[str] | None = None,
    source_cache_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    if limit >= len(sources):
        return list(sources)
    scored = [
        (
            _source_priority(source),
            _source_directory(source),
            str(source.get("target_path") or source.get("source_path") or ""),
            index,
            source,
        )
        for index, source in enumerate(sources)
    ]
    selected: list[dict[str, Any]] = []
    path_ranked = sorted(scored, key=lambda item: (-item[0], item[1], item[2], item[3]))
    remaining = _repair_scored_pool(
        path_ranked,
        limit=limit,
        recipes=recipes,
        source_cache_dir=source_cache_dir,
    )
    seen_directories: set[str] = set()
    while remaining and len(selected) < limit:
        best_index = max(
            range(len(remaining)),
            key=lambda position: (
                remaining[position][5],
                remaining[position][0],
                1 if remaining[position][1] not in seen_directories else 0,
                -remaining[position][3],
            ),
        )
        _, directory, _, _, source, _ = remaining.pop(best_index)
        selected.append(source)
        seen_directories.add(directory)
    return selected


def _sampling_recipes(
    sources: list[dict[str, Any]],
    *,
    requested_recipes: list[str] | None,
    source_cache_dir: str | Path,
    max_auto_recipes: int,
    enabled: bool,
) -> list[str] | None:
    if requested_recipes or not enabled or not sources:
        return requested_recipes
    recipe_sources = _recipe_sampling_sources(sources)
    selection = build_onboarding_recipe_selection(
        {"sources": recipe_sources},
        requested_recipes=None,
        source_cache_dir=source_cache_dir,
        max_auto_recipes=max_auto_recipes,
    )
    selected = _string_list(selection.get("selected_recipes"))
    return selected or None


def _recover_auto_scoped_candidate_sample(
    sources: list[dict[str, Any]],
    *,
    requested_recipes: list[str] | None,
    source_cache_dir: str | Path,
    sample_sources: int,
    max_auto_recipes: int,
    enabled: bool,
) -> tuple[
    list[dict[str, Any]],
    list[str],
    dict[str, Any],
    Any,
] | None:
    if not enabled or requested_recipes or not sources:
        return None
    candidates: list[tuple[int, int, int, int, int, int, dict[str, Any]]] = []
    recovery_sources = _candidate_recovery_sources(sources)
    for index, source in enumerate(recovery_sources):
        report = mine_recipe_sources(
            {"sources": [source]},
            recipes=None,
            source_path="preflight_candidate_recovery",
            source_cache_dir=source_cache_dir,
        )
        if report.generated_count <= 0:
            continue
        source_text = read_source_text_for_selection(source, source_cache_dir)
        score = (
            benchmark_source_recipe_score(
                source,
                recipes=None,
                source_cache_dir=source_cache_dir,
            )
            + _generation_report_score(report)
            + _source_repair_candidate_score(source_text)
            + _source_overlay_static_score(source, source_text)
        )
        candidates.append(
            (
                _deterministic_rule_presence_score(report.rule_counts),
                _source_recovery_safety_score(source, source_text),
                _generated_rule_quality_score(report.rule_counts),
                score,
                report.generated_count,
                -index,
                source,
            )
        )
    if not candidates:
        return None
    selected_sources = [
        source
        for *_scores, source in sorted(candidates, reverse=True)[
            : max(1, min(sample_sources, len(candidates)))
        ]
    ]
    recovery_payload = {"sources": selected_sources}
    all_rule_report = mine_recipe_sources(
        recovery_payload,
        recipes=None,
        source_path="preflight_candidate_recovery",
        source_cache_dir=source_cache_dir,
    )
    selected_recipes = _recovery_selected_recipes(
        all_rule_report.rule_counts,
        max_auto_recipes=max_auto_recipes,
    )
    if not selected_recipes:
        return None
    mining_report = mine_recipe_sources(
        recovery_payload,
        recipes=selected_recipes,
        source_path="preflight_candidate_recovery",
        source_cache_dir=source_cache_dir,
    )
    if mining_report.generated_count <= 0:
        return None
    recipe_selection = build_onboarding_recipe_selection(
        recovery_payload,
        requested_recipes=selected_recipes,
        source_cache_dir=source_cache_dir,
        max_auto_recipes=max_auto_recipes,
    )
    recipe_selection["mode"] = "auto_candidate_recovery"
    recipe_selection["candidate_recovery"] = {
        "reason": "initial_auto_scoped_sample_generated_no_candidates",
        "pool_size": len(recovery_sources),
        "selected_source_count": len(selected_sources),
        "selected_recipes": selected_recipes,
        "generated_candidate_count": mining_report.generated_count,
        "rule_counts": dict(mining_report.rule_counts),
    }
    recipe_selection["selected_recipes"] = selected_recipes
    return selected_sources, selected_recipes, recipe_selection, mining_report


def _recipe_sampling_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        enumerate(sources),
        key=lambda item: (
            -_source_priority(item[1]),
            _source_directory(item[1]),
            str(item[1].get("target_path") or item[1].get("source_path") or ""),
            item[0],
        ),
    )
    return [source for _, source in ranked[:DEFAULT_REPAIR_SCORE_POOL]]


def _candidate_recovery_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        enumerate(sources),
        key=lambda item: (
            -_source_priority(item[1]),
            _source_directory(item[1]),
            str(item[1].get("target_path") or item[1].get("source_path") or ""),
            item[0],
        ),
    )
    return [source for _, source in ranked[:DEFAULT_CANDIDATE_RECOVERY_POOL]]


def _repair_scored_pool(
    path_ranked: list[tuple[int, str, str, int, dict[str, Any]]],
    *,
    limit: int,
    recipes: list[str] | None,
    source_cache_dir: str | Path | None,
) -> list[tuple[int, str, str, int, dict[str, Any], int]]:
    if source_cache_dir is None:
        return [(*item, 0) for item in path_ranked]
    pool_limit = min(len(path_ranked), max(DEFAULT_REPAIR_SCORE_POOL, limit * 5))
    pool = path_ranked[:pool_limit]
    return [
        (
            priority,
            directory,
            target,
            index,
            source,
            _safe_benchmark_source_recipe_score(
                source,
                recipes=recipes,
                source_cache_dir=source_cache_dir,
            ),
        )
        for priority, directory, target, index, source in pool
    ]


def _safe_benchmark_source_recipe_score(
    source: dict[str, Any],
    *,
    recipes: list[str] | None,
    source_cache_dir: str | Path,
) -> int:
    try:
        recipe_score = benchmark_source_recipe_score(
            source,
            recipes=recipes,
            source_cache_dir=source_cache_dir,
        )
        source_text = read_source_text_for_selection(source, source_cache_dir)
        return (
            recipe_score
            + _source_generation_score(
                source,
                recipes=recipes,
                source_cache_dir=source_cache_dir,
            )
            + _source_repair_candidate_score(source_text)
            + _source_overlay_static_score(source, source_text)
        )
    except Exception:
        return 0


def _source_generation_score(
    source: dict[str, Any],
    *,
    recipes: list[str] | None,
    source_cache_dir: str | Path,
) -> int:
    report = mine_recipe_sources(
        {"sources": [source]},
        recipes=recipes,
        source_path="preflight_generation_score",
        source_cache_dir=source_cache_dir,
    )
    return _generation_report_score(report)


def _source_recovery_safety_score(source: dict[str, Any], source_text: str) -> int:
    source_path = str(source.get("source_path") or "")
    target_path = str(source.get("target_path") or "")
    source_root = _path_root(source_path)
    target_root = _path_root(target_path)
    score = 0
    if source_root == "src":
        score += 240
    elif source_root in AUXILIARY_SOURCE_ROOTS:
        score -= 520
    elif source_root:
        score += 140
    if target_root in AUXILIARY_SOURCE_ROOTS:
        score -= 160
    elif target_root:
        score += 60
    score -= min(240, _relative_import_count(source_text) * 40)
    return score


def _path_root(path: str) -> str:
    clean = str(path).replace("\\", "/").strip("/")
    if not clean:
        return ""
    return clean.split("/", 1)[0].lower()


def _relative_import_count(source_text: str) -> int:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return 0
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    )


def _generation_report_score(report: Any) -> int:
    if report.generated_count <= 0:
        return 0
    rule_count = len(report.rule_counts)
    score = 600 + min(rule_count, 3) * 40
    score += sum(
        GENERATED_RULE_QUALITY_BONUS.get(str(rule_id), 0)
        for rule_id in report.rule_counts
    )
    if set(report.rule_counts) == {"broad_exception_pass"}:
        broad_count = int(report.rule_counts.get("broad_exception_pass", 0))
        score -= min(260, max(0, broad_count - 3) * 35)
    return max(0, score)


def _generated_rule_quality_score(rule_counts: Any) -> int:
    counts = _dict(rule_counts)
    return sum(
        (GENERATED_RULE_QUALITY_BONUS.get(str(rule_id), 0) + 1) * _int(count)
        for rule_id, count in counts.items()
    )


def _deterministic_rule_presence_score(rule_counts: Any) -> int:
    counts = _dict(rule_counts)
    return int(
        any(
            str(rule_id) != "broad_exception_pass"
            and GENERATED_RULE_QUALITY_BONUS.get(str(rule_id), 0) > 0
            and _int(count) > 0
            for rule_id, count in counts.items()
        )
    )


def _recovery_selected_recipes(
    rule_counts: Any,
    *,
    max_auto_recipes: int,
) -> list[str]:
    counts = _dict(rule_counts)
    ranked = sorted(
        counts,
        key=lambda rule_id: (
            -GENERATED_RULE_QUALITY_BONUS.get(str(rule_id), 0),
            -_int(counts.get(rule_id, 0)),
            str(rule_id),
        ),
    )
    deterministic = [
        str(rule_id)
        for rule_id in ranked
        if str(rule_id) != "broad_exception_pass"
        and GENERATED_RULE_QUALITY_BONUS.get(str(rule_id), 0) > 0
    ]
    if deterministic:
        return deterministic[: max(1, max_auto_recipes)]
    return [str(rule_id) for rule_id in ranked[: max(1, max_auto_recipes)]]


def _source_overlay_static_score(source: dict[str, Any], source_text: str) -> int:
    findings = _source_overlay_static_findings(source, source_text)
    if not findings:
        return 0
    unique_rules = {finding.rule_id for finding in findings}
    if unique_rules == {"broad_exception_pass"}:
        confidence_score = sum(int(finding.confidence * 25) for finding in findings[:3])
        return 120 + min(len(findings), 3) * 35 + confidence_score
    prior_score = sum(
        int(OVERLAY_RULE_TRIGGER_PRIORS.get(rule_id, 0.50) * 100)
        for rule_id in unique_rules
    )
    confidence_score = sum(int(finding.confidence * 40) for finding in findings[:6])
    return (
        320
        + len(unique_rules) * 140
        + min(len(findings), 6) * 60
        + prior_score
        + confidence_score
    )


def _source_overlay_static_findings(source: dict[str, Any], source_text: str) -> list[Any]:
    if not source_text.strip():
        return []
    source_path = str(source.get("source_path") or source.get("target_path") or "source.py")
    try:
        analysis = ASTAnalyzer().analyze_file(source_path, source_text)
    except SyntaxError:
        return []
    return [
        finding
        for finding in RuleBasedBugDetector().detect(analysis.functions)
        if finding.rule_id in SUPPORTED_OVERLAY_RULES
    ]


def _source_repair_candidate_score(source_text: str) -> int:
    text = source_text.lower()
    if not text:
        return 0
    score = 0
    compact = text.replace(" ", "")
    has_empty_guard = (
        "ifnot" in compact
        or "==0" in compact
        or "<=0" in compact
        or "raise valueerror" in text
        or "raise statisticserror" in text
    )
    if "len(" in text and ("/" in text or "%" in text):
        score += 80
        score += -40 if has_empty_guard else 70
    if ".sort(" in text and "=" in text:
        score += 60
    if "range(" in text and ("[i +" in text or "[index +" in text):
        score += 70
    if ".get(" not in text and ("[" in text and "key" in text):
        score += 30
    if "except exception" in text or "except:" in text:
        score += 50
    if "traceback (most recent call last)" in text and (
        "zerodivisionerror" in text
        or "indexerror" in text
        or "keyerror" in text
        or "valueerror" in text
    ):
        score += 220
    if "\ndef test_" in text:
        score -= 90
    return score


def _mining_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_count": _int(payload.get("source_count", 0)),
        "recipe_count": _int(payload.get("recipe_count", 0)),
        "generated_source_count": _int(payload.get("generated_source_count", 0)),
        "generated_count": _int(payload.get("generated_count", 0)),
        "rule_counts": _dict(payload.get("rule_counts")),
        "bug_type_counts": _dict(payload.get("bug_type_counts")),
        "quality_summary": _dict(payload.get("quality_summary")),
    }


def _preflight_issues(
    *,
    discovery_item_count: int,
    imported_source_count: int,
    sampled_source_count: int,
    generated_candidate_count: int,
    repository_profile: dict[str, Any],
    recipe_selection: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if discovery_item_count <= 0:
        issues.append(
            _issue(
                "fail",
                "no_discovery_items",
                "Discovery did not return repository files.",
            )
        )
    if imported_source_count <= 0:
        issues.append(
            _issue(
                "fail",
                "no_python_sources",
                "No Python sources survived import filters.",
            )
        )
    if sampled_source_count <= 0 and imported_source_count > 0:
        issues.append(
            _issue(
                "fail",
                "no_sampled_sources",
                "Imported sources were present but no preflight sample was selected.",
            )
        )
    if generated_candidate_count <= 0 and imported_source_count > 0:
        issues.append(
            _issue(
                "warning",
                "no_preflight_candidates",
                "Preflight mining did not generate benchmark candidates from the sample.",
            )
        )
    if _int(recipe_selection.get("recommended_count", 0)) <= 0 and imported_source_count > 0:
        issues.append(
            _issue(
                "warning",
                "weak_recipe_signal",
                "Recipe scoring found no strong source-level signals.",
            )
        )
    if (
        imported_source_count > 0
        and _float(repository_profile.get("python_source_ratio", 0.0)) < 0.10
        and discovery_item_count > 0
    ):
        issues.append(
            _issue(
                "warning",
                "low_python_source_ratio",
                "The repository tree contains relatively few Python files.",
            )
        )
    return issues


def _recommended_run(
    *,
    mode: str,
    source: str,
    discovery_path: Path,
    name: str,
    owner: str | None,
    repo: str | None,
    ref: str | None,
    preset: str,
    include: list[str] | None,
    exclude: list[str] | None,
    preserve_paths: bool,
    target_prefix: str,
    selected_recipes: list[str],
    sample_sources: int,
    max_candidates: int,
    generated_candidate_count: int,
    repository_profile: dict[str, Any],
    original_run: dict[str, Any] | None,
    sampled_sources: list[dict[str, Any]],
    auto_scoped_include: bool,
    auto_scoped_include_source: str,
) -> dict[str, Any]:
    effective_target_prefix = target_prefix or str(
        repository_profile.get("recommended_target_prefix") or ""
    )
    run = {
        "name": _slug(name),
        "mode": mode,
        "preset": preset,
        "max_sources": sample_sources,
        "max_candidates": max_candidates,
        "recipe": selected_recipes,
        "thresholds": {
            "min_generated_candidates": max(1, min(generated_candidate_count, 3)),
        },
        "fallback": {
            "enabled": True,
            "preset": "smoke",
            "max_sources": max(sample_sources * 2, 50),
            "max_candidates": max(max_candidates * 2, 20),
        },
        "project_profile": {
            "recommended_test_command": str(
                repository_profile.get("recommended_test_command") or ""
            ),
            "recommended_target_prefix": effective_target_prefix,
            "test_source_count": _int(repository_profile.get("test_source_count", 0)),
            "project_config_count": _int(
                repository_profile.get("project_config_count", 0)
            ),
            "doctor_status": str(repository_profile.get("doctor_status") or ""),
            "doctor_blocker": str(repository_profile.get("doctor_blocker") or ""),
            "doctor_score": _float(repository_profile.get("doctor_score", 0.0)),
            "doctor_next_action": str(
                repository_profile.get("doctor_next_action") or ""
            ),
        },
    }
    for key, value in _dict(original_run).items():
        if value is not None and key not in {"mode", "name"}:
            run[key] = value
    if mode == "from-discovery":
        run["discovery"] = str(discovery_path)
    elif mode == "tree":
        run.update({"owner": owner, "repo": repo})
        if ref:
            run["ref"] = ref
    elif mode == "search":
        if owner:
            run["owner"] = owner
        if repo:
            run["repo"] = repo
        if ref:
            run["ref"] = ref
    if include:
        run["include"] = list(include)
    elif auto_scoped_include:
        scoped_include = _sampled_include_paths(sampled_sources)
        if scoped_include:
            run["include"] = scoped_include
            run["auto_scoped_include"] = True
            run["auto_scoped_include_count"] = len(scoped_include)
            run["auto_scoped_include_source"] = auto_scoped_include_source
    if exclude:
        run["exclude"] = list(exclude)
    if preserve_paths:
        run["preserve_paths"] = True
    if effective_target_prefix:
        run["target_prefix"] = effective_target_prefix
    run["preflight_source"] = source
    return {key: value for key, value in run.items() if value not in (None, [], "")}


def _sampled_include_paths(sources: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for source in sources:
        path = str(source.get("source_path") or source.get("target_path") or "")
        path = path.replace("\\", "/").strip().lstrip("/")
        if not path or ".." in path.split("/"):
            continue
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def _recommended_commands(
    *,
    recommended_manifest_path: Path,
    output_root: Path,
) -> list[str]:
    batch_output = output_root / "smoke_batch"
    return [
        (
            "python -m code_intelligence_agent.evaluation.github_onboarding_smoke_runner "
            f"{recommended_manifest_path} {batch_output} "
            f"--output-json {batch_output / 'runner.json'} "
            f"--output-markdown {batch_output / 'runner.md'}"
        )
    ]


def _next_actions(
    *,
    status: str,
    ready_for_smoke: bool,
    generated_candidate_count: int,
) -> list[str]:
    if status == "fail":
        return [
            "Fix discovery/import filters before running smoke onboarding.",
            "Use tree/search discovery or adjust --include/--exclude to expose Python files.",
        ]
    if ready_for_smoke:
        return [
            "Run the recommended smoke manifest with github_onboarding_smoke_runner.",
            "Inspect onboarding_smoke_gaps.md; if fallback recovers candidates, rerun the recommended manifest and compare reports.",
        ]
    if generated_candidate_count <= 0:
        return [
            "Broaden recipe mining by removing explicit --recipe filters.",
            "Increase --sample-sources or target code-search queries with guard/index/dict/list patterns.",
        ]
    return ["Review preflight_report.md before running smoke onboarding."]


def _status_from_issues(issues: list[dict[str, Any]]) -> str:
    severities = {str(issue.get("severity", "")) for issue in issues}
    if "fail" in severities:
        return "fail"
    if "warning" in severities:
        return "warning"
    return "pass"


def _replace_recommended_repo_run(
    report: GitHubOnboardingPreflightReport,
    repo_spec: str,
) -> GitHubOnboardingPreflightReport:
    run = dict(report.recommended_run)
    run["mode"] = "repo"
    run["repo"] = repo_spec
    for key in ("owner",):
        run.pop(key, None)
    manifest = dict(report.recommended_manifest)
    manifest["runs"] = [run]
    replaced = GitHubOnboardingPreflightReport(
        **{
            **report.to_dict(),
            "mode": "repo",
            "recommended_run": run,
            "recommended_manifest": manifest,
        }
    )
    _write_replaced_preflight(replaced)
    return replaced


def _write_replaced_preflight(report: GitHubOnboardingPreflightReport) -> None:
    paths = report.output_paths
    Path(paths["recommended_manifest"]).write_text(
        json.dumps(report.recommended_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["preflight_json"]).write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    Path(paths["preflight_markdown"]).write_text(
        render_github_onboarding_preflight_markdown(report),
        encoding="utf-8",
    )


def _shared_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "include": args.include,
        "exclude": args.exclude,
        "preserve_paths": args.preserve_paths,
        "target_prefix": args.target_prefix,
        "recipes": args.recipe,
        "source_cache_dir": args.source_cache_dir,
        "sample_sources": args.sample_sources,
        "max_candidates": args.max_candidates,
        "max_auto_recipes": args.max_auto_recipes,
        "preset": args.preset,
        "run_name": args.run_name,
        "auto_scoped_include": args.auto_scoped_include,
    }


def _add_network_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--api-base-url", default="https://api.github.com")
    parser.add_argument("--timeout", type=int, default=20)


def _add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--include", action="append")
    parser.add_argument("--exclude", action="append")
    parser.add_argument("--preserve-paths", action="store_true")
    parser.add_argument("--target-prefix", default="")
    parser.add_argument("--recipe", action="append")
    parser.add_argument("--source-cache-dir")
    parser.add_argument(
        "--auto-scoped-include",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Write sampled Python source paths into the recommended smoke "
            "manifest as include filters when no explicit --include is supplied."
        ),
    )
    parser.add_argument(
        "--sample-sources",
        type=int,
        default=DEFAULT_PREFLIGHT_SAMPLE_SOURCES,
        help="Maximum imported Python sources to sample for preflight mining.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=DEFAULT_PREFLIGHT_MAX_CANDIDATES,
        help="Recommended max-candidates value for the generated smoke run.",
    )
    parser.add_argument(
        "--max-auto-recipes",
        type=int,
        default=DEFAULT_AUTO_RECIPE_LIMIT,
        help="Maximum automatically selected recipes.",
    )
    parser.add_argument(
        "--preset",
        choices=["smoke", "mining", "manual"],
        default="smoke",
        help="Preset to place in the recommended runner entry.",
    )
    parser.add_argument("--run-name")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")


def _source_priority(source: dict[str, Any]) -> int:
    text = (
        str(source.get("target_path", ""))
        + "\n"
        + str(source.get("source_path", ""))
    ).lower()
    score = 0
    if not _is_test_path(text):
        score += 20
    for token, weight in REPAIR_SOURCE_PATH_HINT_WEIGHTS.items():
        if token in text:
            score += weight
    if PurePosixPath(text).name == "__init__.py":
        score -= 10
    return score


def _discovery_item_count(payload: dict[str, Any]) -> int:
    total = 0
    for key in ("tree", "items", "files"):
        values = payload.get(key)
        if isinstance(values, list):
            total += len(values)
    repositories = payload.get("repositories")
    if isinstance(repositories, list):
        for repository in repositories:
            if isinstance(repository, dict):
                files = repository.get("paths", repository.get("files", []))
                total += len(files) if isinstance(files, list) else 1
            else:
                total += 1
    return total


def _issue(severity: str, code: str, message: str) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message}


def _token_from_env(env_name: str | None) -> str | None:
    if not env_name:
        return None
    return os.environ.get(env_name)


def _default_run_name(
    *,
    mode: str,
    source: str,
    owner: str | None,
    repo: str | None,
) -> str:
    if owner and repo:
        return f"{owner}_{repo}"
    return f"{mode}_{Path(source).stem or 'repo'}"


def _source_directory(source: dict[str, Any]) -> str:
    return _directory(str(source.get("source_path") or source.get("target_path") or ""))


def _directory(path_text: str) -> str:
    parent = str(PurePosixPath(path_text).parent)
    return "" if parent == "." else parent


def _extension(path_text: str) -> str:
    suffix = PurePosixPath(path_text).suffix.lower()
    return suffix or "<none>"


def _is_test_path(path_text: str) -> bool:
    text = path_text.lower().replace("\\", "/")
    name = PurePosixPath(text).name
    return (
        "/tests/" in f"/{text}"
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}:{_int(value)}" for key, value in sorted(counts.items()))


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value]
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "repo"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


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
    return str(value).replace("\n", " ").replace("|", "\\|")


if __name__ == "__main__":
    main()
