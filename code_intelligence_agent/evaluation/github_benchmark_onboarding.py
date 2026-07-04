from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_multi_source_augmenter import (
    augment_template_with_dependency_sources,
    render_multi_source_augmentation_markdown,
)
from code_intelligence_agent.evaluation.benchmark_recipe_generator import (
    SUPPORTED_RECIPES,
)
from code_intelligence_agent.evaluation.benchmark_source_miner import (
    SourceMiningReport,
    mine_recipe_sources,
    render_source_mining_markdown,
)
from code_intelligence_agent.evaluation.github_discovery_fetcher import (
    GitHubAPIError,
    GitHubDiscoveryFetchReport,
    fetch_code_search_discovery,
    fetch_tree_discovery,
    render_github_discovery_fetch_markdown,
)
from code_intelligence_agent.evaluation.github_fetcher import (
    GitHubBenchmarkFetcher,
    source_from_dict,
)
from code_intelligence_agent.evaluation.github_repository_checkout import (
    checkout_github_repository,
    write_repository_checkout_artifacts,
)
from code_intelligence_agent.evaluation.github_repository_checkout_sources import (
    build_repository_checkout_discovery,
    write_repository_checkout_discovery_artifacts,
)
from code_intelligence_agent.evaluation.github_repository_profile import (
    build_github_repository_profile,
    render_github_repository_profile_markdown,
)
from code_intelligence_agent.evaluation.github_source_importer import (
    GitHubSourceImportReport,
    import_github_sources,
    render_github_source_import_markdown,
)
from code_intelligence_agent.evaluation.onboarding_smoke_validator import (
    OnboardingSmokeValidationReport,
    render_onboarding_smoke_validation_markdown,
    validate_onboarding_smoke_report,
)
from code_intelligence_agent.evaluation.repository_test_command import (
    validate_repository_test_command,
    write_repository_test_command_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_dynamic_evidence import (
    build_repository_test_dynamic_evidence,
    write_repository_test_dynamic_evidence_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_environment import (
    plan_repository_test_environment,
    write_repository_test_environment_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_environment_setup import (
    execute_repository_test_environment_setup,
    plan_repository_test_environment_setup,
    write_repository_test_environment_setup_artifacts,
    write_repository_test_environment_setup_result_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_execution_plan import (
    plan_repository_test_execution,
    write_repository_test_execution_plan_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_execution_result import (
    execute_repository_test_plan,
    write_repository_test_execution_result_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_setup_doctor import (
    build_repository_test_setup_doctor,
    write_repository_test_setup_doctor_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_failure_overlay import (
    build_repository_test_failure_overlay,
    write_repository_test_failure_overlay_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_fault_localization import (
    build_repository_test_fault_localization,
    write_repository_test_fault_localization_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_patch_candidates import (
    build_repository_test_patch_candidates,
    write_repository_test_patch_candidates_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_patch_validation import (
    build_repository_test_patch_validation,
    write_repository_test_patch_validation_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_repair_summary import (
    build_repository_test_repair_summary,
    write_repository_test_repair_summary_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_pytest_plugin_repair import (
    execute_repository_test_pytest_plugin_repair,
    plan_repository_test_pytest_plugin_repair,
    write_repository_test_pytest_plugin_repair_artifacts,
    write_repository_test_pytest_plugin_repair_retry_execution_result_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_retry_plan import (
    plan_repository_test_retry,
    write_repository_test_retry_plan_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_retry_execution_result import (
    execute_repository_test_retry_plan,
    write_repository_test_retry_execution_result_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_timeout_narrowing import (
    execute_repository_test_timeout_narrowing,
    plan_repository_test_timeout_narrowing,
    write_repository_test_timeout_narrowing_artifacts,
)
from code_intelligence_agent.evaluation.run_template_benchmark import (
    run_template_benchmark,
)


DEFAULT_DEPENDENCY_MAX_DEPTH = 4
DEFAULT_AUTO_RECIPE_LIMIT = 3


@dataclass(frozen=True)
class OnboardingQualityGateThresholds:
    min_imported_sources: int = 1
    min_generated_candidates: int = 1
    min_quality_score: float = 0.50
    min_source_hit_rate: float = 0.50
    min_selected_source_groups: int = 1
    min_selected_source_directories: int = 1
    min_selected_rules: int = 1
    min_selected_bug_types: int = 1
    min_source_group_coverage: float = 0.0
    min_source_directory_coverage: float = 0.0
    min_candidate_rule_coverage: float = 0.0
    min_candidate_bug_type_coverage: float = 0.0
    min_candidate_source_coverage: float = 0.0
    require_ready_for_benchmark: bool = True
    require_benchmark_run: bool = True
    min_benchmark_cases: int = 1
    min_top1: float = 0.50
    min_map: float = 0.50
    min_patch_success_rate: float = 0.50

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OnboardingQualityGateCheck:
    name: str
    passed: bool
    expected: str
    actual: str
    details: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OnboardingQualityGateResult:
    passed: bool
    thresholds: OnboardingQualityGateThresholds
    checks: list[OnboardingQualityGateCheck]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "thresholds": self.thresholds.to_dict(),
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True)
class GitHubBenchmarkOnboardingReport:
    mode: str
    preset: str
    source: str
    output_dir: str
    discovery_item_count: int
    imported_source_count: int
    selected_source_count: int
    skipped_source_count: int
    generated_candidate_count: int
    ready_for_benchmark: bool
    source_limit: int | None
    candidate_limit: int | None
    requested_urls: list[str]
    discovery_metadata: dict[str, Any] | None
    repository_profile: dict[str, Any]
    quality_summary: dict[str, Any]
    output_paths: dict[str, str]
    import_report: GitHubSourceImportReport
    mining_report: SourceMiningReport
    benchmark_run: dict[str, Any] | None = None
    quality_gate: dict[str, Any] | None = None
    showcase_lite: dict[str, Any] | None = None
    smoke_validation: dict[str, Any] | None = None
    run_config: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None
    benchmarkization_readiness: dict[str, Any] | None = None
    repository_test_command: dict[str, Any] | None = None
    repository_test_environment: dict[str, Any] | None = None
    repository_test_environment_setup: dict[str, Any] | None = None
    repository_test_environment_setup_result: dict[str, Any] | None = None
    repository_test_execution_plan: dict[str, Any] | None = None
    repository_test_execution_result: dict[str, Any] | None = None
    repository_test_retry_plan: dict[str, Any] | None = None
    repository_test_retry_execution_result: dict[str, Any] | None = None
    repository_test_setup_doctor: dict[str, Any] | None = None
    repository_test_pytest_plugin_repair: dict[str, Any] | None = None
    repository_test_pytest_plugin_repair_retry_execution_result: dict[str, Any] | None = None
    repository_test_timeout_narrowing: dict[str, Any] | None = None
    repository_test_dynamic_evidence: dict[str, Any] | None = None
    repository_test_failure_overlay: dict[str, Any] | None = None
    repository_test_fault_localization: dict[str, Any] | None = None
    repository_test_patch_candidates: dict[str, Any] | None = None
    repository_test_patch_validation: dict[str, Any] | None = None
    repository_test_repair_summary: dict[str, Any] | None = None
    repository_checkout: dict[str, Any] | None = None
    repository_checkout_sources: dict[str, Any] | None = None
    repository_config_snapshot: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "preset": self.preset,
            "source": self.source,
            "output_dir": self.output_dir,
            "discovery_item_count": self.discovery_item_count,
            "imported_source_count": self.imported_source_count,
            "selected_source_count": self.selected_source_count,
            "skipped_source_count": self.skipped_source_count,
            "generated_candidate_count": self.generated_candidate_count,
            "ready_for_benchmark": self.ready_for_benchmark,
            "source_limit": self.source_limit,
            "candidate_limit": self.candidate_limit,
            "requested_urls": self.requested_urls,
            "discovery_metadata": self.discovery_metadata,
            "repository_profile": self.repository_profile,
            "quality_summary": self.quality_summary,
            "output_paths": self.output_paths,
            "import_report": self.import_report.to_dict(),
            "mining_report": self.mining_report.to_dict(),
            "benchmark_run": self.benchmark_run,
            "quality_gate": self.quality_gate,
            "showcase_lite": self.showcase_lite,
            "smoke_validation": self.smoke_validation,
            "run_config": self.run_config,
            "diagnostics": self.diagnostics,
            "benchmarkization_readiness": (
                self.benchmarkization_readiness
                or _benchmarkization_readiness(self)
            ),
            "repository_test_command": self.repository_test_command,
            "repository_test_environment": self.repository_test_environment,
            "repository_test_environment_setup": self.repository_test_environment_setup,
            "repository_test_environment_setup_result": (
                self.repository_test_environment_setup_result
            ),
            "repository_test_execution_plan": self.repository_test_execution_plan,
            "repository_test_execution_result": self.repository_test_execution_result,
            "repository_test_retry_plan": self.repository_test_retry_plan,
            "repository_test_retry_execution_result": (
                self.repository_test_retry_execution_result
            ),
            "repository_test_setup_doctor": self.repository_test_setup_doctor,
            "repository_test_pytest_plugin_repair": (
                self.repository_test_pytest_plugin_repair
            ),
            "repository_test_pytest_plugin_repair_retry_execution_result": (
                self.repository_test_pytest_plugin_repair_retry_execution_result
            ),
            "repository_test_timeout_narrowing": (
                self.repository_test_timeout_narrowing
            ),
            "repository_test_dynamic_evidence": self.repository_test_dynamic_evidence,
            "repository_test_failure_overlay": self.repository_test_failure_overlay,
            "repository_test_fault_localization": (
                self.repository_test_fault_localization
            ),
            "repository_test_patch_candidates": self.repository_test_patch_candidates,
            "repository_test_patch_validation": self.repository_test_patch_validation,
            "repository_test_repair_summary": self.repository_test_repair_summary,
            "repository_checkout": self.repository_checkout,
            "repository_checkout_sources": self.repository_checkout_sources,
            "repository_config_snapshot": self.repository_config_snapshot,
        }


def parse_github_repo_spec(spec: str) -> tuple[str, str]:
    owner, repo, _ = parse_github_repo_spec_with_ref(spec)
    return owner, repo


def parse_github_repo_spec_with_ref(spec: str) -> tuple[str, str, str | None]:
    parts = _github_repo_spec_parts(spec)
    owner = parts[0].strip()
    repo = parts[1].strip()
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise ValueError(
            "repo spec must look like owner/repo or https://github.com/owner/repo"
        )
    inferred_ref = _github_ref_from_url_parts(parts)
    return owner, repo, inferred_ref


def github_ref_candidates_from_repo_spec(spec: str) -> list[str]:
    parts = _github_repo_spec_parts(spec)
    return _github_ref_candidates_from_url_parts(parts)


def _github_repo_spec_parts(spec: str) -> list[str]:
    raw = spec.strip()
    if not raw:
        raise ValueError(
            "repo spec must look like owner/repo or https://github.com/owner/repo"
        )

    if raw.startswith("git@github.com:"):
        path = raw.removeprefix("git@github.com:")
    elif "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        if (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}:
            raise ValueError("repo URL host must be github.com")
        path = parsed.path
    elif raw.lower().startswith("github.com/"):
        path = raw.split("/", 1)[1]
    elif raw.lower().startswith("www.github.com/"):
        path = raw.split("/", 1)[1]
    else:
        path = raw

    parts = [
        urllib.parse.unquote(part)
        for part in path.strip("/").split("/")
        if part.strip()
    ]
    if len(parts) < 2:
        raise ValueError(
            "repo spec must look like owner/repo or https://github.com/owner/repo"
        )
    return parts


def _github_ref_from_url_parts(parts: list[str]) -> str | None:
    candidates = _github_ref_candidates_from_url_parts(parts)
    return candidates[0] if candidates else None


def _github_ref_candidates_from_url_parts(parts: list[str]) -> list[str]:
    if len(parts) < 4:
        return []
    marker = parts[2].strip().lower()
    if marker in {"tree", "blob"}:
        return _incremental_ref_candidates(parts[3:])
    if marker == "commit":
        ref = parts[3].strip()
        return [ref] if ref else []
    if marker == "releases" and len(parts) >= 5 and parts[3].lower() == "tag":
        return _incremental_ref_candidates(parts[4:])
    return []


def _incremental_ref_candidates(parts: list[str]) -> list[str]:
    candidates: list[str] = []
    current: list[str] = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        current.append(stripped)
        candidate = "/".join(current)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def onboard_from_discovery(
    discovery_payload: dict[str, Any],
    output_dir: str | Path,
    *,
    source: str = "discovery",
    mode: str = "from-discovery",
    preset: str = "manual",
    requested_urls: list[str] | None = None,
    owner: str | None = None,
    repo: str | None = None,
    ref: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    preserve_paths: bool = False,
    target_prefix: str = "",
    recipes: list[str] | None = None,
    source_cache_dir: str | Path | None = None,
    max_sources: int | None = None,
    max_candidates: int | None = None,
    auto_dependency_sources: bool = True,
    dependency_max_depth: int = DEFAULT_DEPENDENCY_MAX_DEPTH,
    materialize_template: bool = False,
    run_benchmark: bool = False,
    benchmark_output_dir: str | Path | None = None,
    patch_mode: str = "rule",
    judge_mode: str = "none",
    patch_judge_mode: str = "none",
    llm_score_mode: str = "none",
    use_dynamic_coverage: bool = True,
    run_quality_gate: bool = False,
    quality_gate_thresholds: OnboardingQualityGateThresholds | None = None,
    run_showcase_lite: bool = False,
    run_smoke_validation: bool = False,
    run_repository_test_command: bool = True,
    run_repository_test_environment_setup: bool = False,
    run_repository_test_retry: bool = False,
    run_repository_test_retry_prerequisites: bool = False,
    auto_repository_test_retry: bool = False,
    auto_repository_test_retry_max_risk: str = "low",
    auto_repository_test_retry_allowed_runners: list[str] | None = None,
    repository_test_root: str | Path | None = None,
    repository_test_timeout: int = 20,
    repository_test_failure_overlay_candidate_limit: int = 5,
    repository_test_patch_validation_limit: int = 5,
    repository_patch_generation_mode: str = "rule",
    repository_llm_patch_candidate_limit: int | None = None,
    repository_patch_candidate_variant_allowlist: list[str] | None = None,
    repository_test_reflection_mode: str = "rule",
    repository_test_reflection_rounds: int = 1,
    repository_test_reflection_width: int = 1,
    repository_test_environment_setup_timeout: int = 120,
    checkout_repository_tests: bool = False,
    repository_checkout_timeout: int = 120,
    repository_checkout_depth: int = 1,
    repository_checkout_runner=None,
    repository_test_environment_setup_runner=None,
    repository_test_execution_runner=None,
    repository_test_retry_execution_runner=None,
) -> GitHubBenchmarkOnboardingReport:
    _validate_positive_limit("max_sources", max_sources)
    _validate_positive_limit("max_candidates", max_candidates)
    _validate_non_negative_threshold("dependency_max_depth", dependency_max_depth)
    _validate_non_negative_threshold(
        "repository_test_reflection_rounds",
        repository_test_reflection_rounds,
    )
    _validate_positive_limit(
        "repository_test_failure_overlay_candidate_limit",
        repository_test_failure_overlay_candidate_limit,
    )
    _validate_positive_limit(
        "repository_test_patch_validation_limit",
        repository_test_patch_validation_limit,
    )
    _validate_positive_limit(
        "repository_test_reflection_width",
        repository_test_reflection_width,
    )
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root = (
        Path(source_cache_dir)
        if source_cache_dir is not None
        else output_root / "source_cache"
    )

    discovery_path = output_root / "discovery.json"
    discovery_path.write_text(
        json.dumps(discovery_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    early_output_paths: dict[str, str] = {}
    repository_checkout = None
    repository_checkout_sources = None
    repository_config_snapshot = None
    effective_repository_test_root = repository_test_root
    effective_repository_config_root = repository_test_root
    effective_discovery_payload = discovery_payload
    effective_source = source
    effective_target_prefix = target_prefix
    target_prefix_source = "explicit" if target_prefix else "none"
    if checkout_repository_tests:
        checkout_owner, checkout_repo, checkout_ref = _repository_identity_for_checkout(
            discovery_payload,
            owner=owner,
            repo=repo,
            ref=ref,
        )
        checkout_source_root: str | Path | None = None
        if repository_test_root is not None:
            repository_checkout = _repository_checkout_skipped(
                owner=checkout_owner,
                repo=checkout_repo,
                ref=checkout_ref,
                output_root=output_root,
                reason="repository_test_root_provided",
                message=(
                    "Skipping automatic checkout because repository_test_root "
                    "was provided explicitly."
                ),
            )
            checkout_source_root = repository_test_root
        else:
            repository_checkout = checkout_github_repository(
                owner=checkout_owner,
                repo=checkout_repo,
                ref=checkout_ref,
                output_dir=output_root,
                depth=repository_checkout_depth,
                timeout=repository_checkout_timeout,
                runner=repository_checkout_runner,
            )
            if repository_checkout.get("status") == "pass":
                checkout_source_root = str(
                    repository_checkout.get("checkout_path") or ""
                )
                effective_repository_test_root = checkout_source_root
                effective_repository_config_root = checkout_source_root
        early_output_paths.update(
            write_repository_checkout_artifacts(repository_checkout, output_root)
        )
        if checkout_source_root:
            repository_checkout_sources = build_repository_checkout_discovery(
                checkout_source_root,
                owner=checkout_owner,
                repo=checkout_repo,
                ref=checkout_ref,
            )
            _preserve_checkout_ref_provenance(
                repository_checkout_sources,
                discovery_payload,
            )
            early_output_paths.update(
                write_repository_checkout_discovery_artifacts(
                    repository_checkout_sources,
                    output_root,
                )
            )
            if _list(repository_checkout_sources.get("files")):
                effective_discovery_payload = repository_checkout_sources
                effective_source = f"{source}:repository-checkout"
    if run_repository_test_command and not effective_repository_config_root:
        repository_config_snapshot = build_repository_config_snapshot(
            discovery_payload,
            output_root,
        )
        early_output_paths.update(
            write_repository_config_snapshot_artifacts(
                repository_config_snapshot,
                output_root,
            )
        )
        if str(repository_config_snapshot.get("status") or "") == "pass":
            effective_repository_config_root = str(
                repository_config_snapshot.get("config_root") or ""
            )
    if not effective_target_prefix and not preserve_paths:
        inferred_target_prefix, inferred_reason = _infer_target_prefix_from_discovery(
            effective_discovery_payload
        )
        if inferred_target_prefix:
            effective_target_prefix = inferred_target_prefix
            target_prefix_source = inferred_reason

    import_report = import_github_sources(
        effective_discovery_payload,
        source_path=effective_source,
        owner=owner,
        repo=repo,
        ref=ref,
        include=include,
        exclude=exclude,
        preserve_paths=preserve_paths,
        preserve_raw_upstream=True,
        target_prefix=effective_target_prefix,
    )
    import_payload = import_report.to_dict()
    limited_sources_payload = _limit_sources_payload(
        import_payload["sources_payload"],
        max_sources=max_sources,
        recipes=recipes,
        source_cache_dir=cache_root,
    )
    repository_profile = build_github_repository_profile(
        effective_discovery_payload,
        import_payload,
        sampled_sources=_list(limited_sources_payload.get("sources")),
    )
    import_report_path = output_root / "source_import.json"
    import_markdown_path = output_root / "source_import.md"
    sources_path = output_root / "sources.json"
    repository_profile_path = output_root / "repository_profile.json"
    repository_profile_markdown_path = output_root / "repository_profile.md"
    import_report_path.write_text(
        json.dumps(import_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    import_markdown_path.write_text(
        render_github_source_import_markdown(import_report),
        encoding="utf-8",
    )
    sources_path.write_text(
        json.dumps(limited_sources_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    repository_profile_path.write_text(
        json.dumps(repository_profile, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    repository_profile_markdown_path.write_text(
        render_github_repository_profile_markdown(repository_profile),
        encoding="utf-8",
    )
    recipe_selection = build_onboarding_recipe_selection(
        limited_sources_payload,
        requested_recipes=recipes,
        source_cache_dir=cache_root,
    )
    effective_recipes = _list(recipe_selection.get("selected_recipes")) or sorted(
        SUPPORTED_RECIPES
    )
    recipe_selection_path = output_root / "onboarding_recipe_selection.json"
    recipe_selection_markdown_path = output_root / "onboarding_recipe_selection.md"
    recipe_selection_path.write_text(
        json.dumps(recipe_selection, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    recipe_selection_markdown_path.write_text(
        render_onboarding_recipe_selection_markdown(recipe_selection),
        encoding="utf-8",
    )
    dependency_import_report = None
    dependency_sources_payload = limited_sources_payload
    dependency_import_report_path = output_root / "dependency_source_import.json"
    dependency_import_markdown_path = output_root / "dependency_source_import.md"
    dependency_sources_path = output_root / "dependency_sources.json"
    if auto_dependency_sources:
        dependency_import_report = import_github_sources(
            effective_discovery_payload,
            source_path=f"{effective_source}:dependency-pool",
            owner=owner,
            repo=repo,
            ref=ref,
            include=None,
            exclude=exclude,
            preserve_paths=preserve_paths,
            preserve_raw_upstream=True,
            target_prefix=effective_target_prefix,
        )
        dependency_sources_payload = dependency_import_report.to_dict()[
            "sources_payload"
        ]
        dependency_import_payload = dependency_import_report.to_dict()
        dependency_import_report_path.write_text(
            json.dumps(dependency_import_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        dependency_import_markdown_path.write_text(
            render_github_source_import_markdown(dependency_import_report),
            encoding="utf-8",
        )
        dependency_sources_path.write_text(
            json.dumps(dependency_sources_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    mining_report = mine_recipe_sources(
        limited_sources_payload,
        recipes=[str(recipe) for recipe in effective_recipes],
        source_path=str(sources_path),
        source_cache_dir=cache_root,
    )
    mining_payload = _limit_mining_payload(
        mining_report.to_dict(),
        max_candidates=max_candidates,
    )
    dependency_augmentation = None
    if _int(mining_payload.get("generated_count", 0)) > 0:
        dependency_augmentation = augment_template_with_dependency_sources(
            mining_payload["template"],
            dependency_sources_payload,
            template_path="source_mining_template.json",
            sources_path=str(dependency_sources_path if auto_dependency_sources else sources_path),
            source_cache_dir=cache_root,
            max_depth=dependency_max_depth,
        )
        mining_payload["template"] = dependency_augmentation.to_dict()["template"]
    mining_report_path = output_root / "source_mining.json"
    mining_markdown_path = output_root / "source_mining.md"
    catalog_path = output_root / "source_mining_catalog.json"
    template_path = output_root / "source_mining_template.json"
    candidate_sources_path = output_root / "candidate_sources.json"
    dependency_augmentation_path = output_root / "multi_source_augmentation.json"
    dependency_augmentation_markdown_path = (
        output_root / "multi_source_augmentation.md"
    )
    mining_report_path.write_text(
        json.dumps(mining_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    mining_markdown_path.write_text(
        render_source_mining_markdown(mining_report),
        encoding="utf-8",
    )
    catalog_path.write_text(
        json.dumps(mining_payload["catalog"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    template_path.write_text(
        json.dumps(mining_payload["template"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    candidate_sources_path.write_text(
        json.dumps(mining_payload["sources_payload"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if dependency_augmentation is not None:
        dependency_payload = dependency_augmentation.to_dict()
        dependency_augmentation_path.write_text(
            json.dumps(dependency_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        dependency_augmentation_markdown_path.write_text(
            render_multi_source_augmentation_markdown(dependency_augmentation),
            encoding="utf-8",
        )

    output_paths = {
        "discovery": str(discovery_path),
        "source_import_json": str(import_report_path),
        "source_import_markdown": str(import_markdown_path),
        "sources": str(sources_path),
        "repository_profile_json": str(repository_profile_path),
        "repository_profile_markdown": str(repository_profile_markdown_path),
        "source_mining_json": str(mining_report_path),
        "source_mining_markdown": str(mining_markdown_path),
        "catalog": str(catalog_path),
        "template": str(template_path),
        "candidate_sources": str(candidate_sources_path),
        "recipe_selection_json": str(recipe_selection_path),
        "recipe_selection_markdown": str(recipe_selection_markdown_path),
        "source_cache_dir": str(cache_root),
    }
    output_paths.update(early_output_paths)
    if dependency_import_report is not None:
        output_paths["dependency_source_import_json"] = str(
            dependency_import_report_path
        )
        output_paths["dependency_source_import_markdown"] = str(
            dependency_import_markdown_path
        )
        output_paths["dependency_sources"] = str(dependency_sources_path)
    if dependency_augmentation is not None:
        output_paths["multi_source_augmentation_json"] = str(
            dependency_augmentation_path
        )
        output_paths["multi_source_augmentation_markdown"] = str(
            dependency_augmentation_markdown_path
        )

    repository_test_environment = None
    if run_repository_test_command:
        repository_test_environment = plan_repository_test_environment(
            repository_profile,
            repository_root=effective_repository_config_root,
        )
        output_paths.update(
            write_repository_test_environment_artifacts(
                repository_test_environment,
                output_root,
            )
        )

    repository_test_environment_setup = None
    if run_repository_test_command:
        setup_environment = dict(repository_test_environment or {})
        if effective_repository_config_root and not effective_repository_test_root:
            setup_environment["repository_root"] = ""
            setup_environment["repository_root_present"] = False
        repository_test_environment_setup = plan_repository_test_environment_setup(
            setup_environment,
            output_dir=output_root,
            repository_root=effective_repository_test_root,
        )
        output_paths.update(
            write_repository_test_environment_setup_artifacts(
                repository_test_environment_setup,
                output_root,
            )
        )

    repository_test_environment_setup_result = None
    if run_repository_test_command:
        repository_test_environment_setup_result = execute_repository_test_environment_setup(
            repository_test_environment_setup or {},
            enabled=run_repository_test_environment_setup,
            timeout=repository_test_environment_setup_timeout,
            runner=repository_test_environment_setup_runner,
        )
        output_paths.update(
            write_repository_test_environment_setup_result_artifacts(
                repository_test_environment_setup_result,
                output_root,
            )
        )

    repository_test_execution_plan = None
    if run_repository_test_command:
        repository_test_execution_plan = plan_repository_test_execution(
            repository_profile,
            repository_test_environment=repository_test_environment,
            repository_test_environment_setup=repository_test_environment_setup,
            repository_test_environment_setup_result=(
                repository_test_environment_setup_result
            ),
            repository_root=effective_repository_test_root,
        )
        output_paths.update(
            write_repository_test_execution_plan_artifacts(
                repository_test_execution_plan,
                output_root,
            )
        )

    repository_test_execution_result = None
    test_python = None
    test_python_source = "current_interpreter"
    if run_repository_test_command:
        test_python, test_python_source = _planned_repository_test_python(
            repository_test_environment_setup,
            repository_test_environment_setup_result,
        )
        repository_test_execution_result = execute_repository_test_plan(
            repository_test_execution_plan or {},
            repository_root=effective_repository_test_root,
            timeout=repository_test_timeout,
            python_executable=test_python,
            python_executable_source=test_python_source,
            runner=repository_test_execution_runner,
        )
        output_paths.update(
            write_repository_test_execution_result_artifacts(
                repository_test_execution_result,
                output_root,
            )
        )

    repository_test_retry_plan = None
    if run_repository_test_command:
        repository_test_retry_plan = plan_repository_test_retry(
            repository_test_execution_plan or {},
            repository_test_execution_result or {},
            repository_test_environment=repository_test_environment,
            repository_test_environment_setup=repository_test_environment_setup,
            repository_test_environment_setup_result=repository_test_environment_setup_result,
        )
        output_paths.update(
            write_repository_test_retry_plan_artifacts(
                repository_test_retry_plan,
                output_root,
            )
        )

    if (
        run_repository_test_command
        and run_repository_test_retry_prerequisites
        and _retry_plan_requires_environment_setup(repository_test_retry_plan)
        and _setup_result_can_run_as_retry_prerequisite(
            repository_test_environment_setup_result
        )
    ):
        repository_test_environment_setup_result = execute_repository_test_environment_setup(
            repository_test_environment_setup or {},
            enabled=True,
            timeout=repository_test_environment_setup_timeout,
            runner=repository_test_environment_setup_runner,
        )
        repository_test_environment_setup_result = dict(
            repository_test_environment_setup_result
        )
        repository_test_environment_setup_result.update(
            {
                "triggered_by": "repository_test_retry_prerequisite",
                "auto_retry_prerequisite": True,
            }
        )
        output_paths.update(
            write_repository_test_environment_setup_result_artifacts(
                repository_test_environment_setup_result,
                output_root,
            )
        )
        test_python, test_python_source = _planned_repository_test_python(
            repository_test_environment_setup,
            repository_test_environment_setup_result,
        )

    effective_run_repository_test_retry = (
        run_repository_test_retry
        or _should_auto_run_repository_test_retry(
            repository_test_retry_plan,
            repository_test_environment_setup_result,
            enabled=auto_repository_test_retry,
            max_risk=auto_repository_test_retry_max_risk,
            allowed_runners=auto_repository_test_retry_allowed_runners,
        )
    )
    repository_test_retry_execution_result = None
    if run_repository_test_command:
        repository_test_retry_execution_result = execute_repository_test_retry_plan(
            repository_test_retry_plan or {},
            repository_root=effective_repository_test_root,
            timeout=repository_test_timeout,
            enabled=effective_run_repository_test_retry,
            python_executable=test_python,
            python_executable_source=test_python_source,
            repository_test_environment_setup_result=repository_test_environment_setup_result,
            runner=repository_test_retry_execution_runner,
        )
        repository_test_retry_execution_result = dict(
            repository_test_retry_execution_result
        )
        repository_test_retry_execution_result.update(
            {
                "retry_enabled_source": (
                    "explicit"
                    if run_repository_test_retry
                    else (
                        "auto_repository_test_retry"
                        if effective_run_repository_test_retry
                        else "disabled"
                    )
                ),
                "auto_repository_test_retry_enabled": auto_repository_test_retry,
                "auto_repository_test_retry_applied": bool(
                    effective_run_repository_test_retry
                    and not run_repository_test_retry
                ),
                "auto_repository_test_retry_max_risk": str(
                    auto_repository_test_retry_max_risk or ""
                ),
                "auto_repository_test_retry_allowed_runners": [
                    str(item)
                    for item in (auto_repository_test_retry_allowed_runners or [])
                ],
            }
        )
        output_paths.update(
            write_repository_test_retry_execution_result_artifacts(
                repository_test_retry_execution_result,
                output_root,
            )
        )

    repository_test_pytest_plugin_repair = None
    repository_test_pytest_plugin_repair_retry_execution_result = None
    effective_repository_test_retry_execution_result = (
        repository_test_retry_execution_result
    )
    if (
        run_repository_test_command
        and _should_attempt_pytest_plugin_repair(
            repository_test_retry_execution_result,
            repository_test_environment_setup_result,
        )
    ):
        repository_test_pytest_plugin_repair_plan = (
            plan_repository_test_pytest_plugin_repair(
                repository_test_environment_setup,
                repository_test_retry_execution_result,
            )
        )
        repository_test_pytest_plugin_repair = (
            execute_repository_test_pytest_plugin_repair(
                repository_test_pytest_plugin_repair_plan,
                enabled=True,
                timeout=repository_test_environment_setup_timeout,
                runner=repository_test_environment_setup_runner,
            )
        )
        repository_test_pytest_plugin_repair = dict(
            repository_test_pytest_plugin_repair
        )
        repository_test_pytest_plugin_repair.update(
            {
                "triggered_by": "repository_test_retry_missing_pytest_fixture",
                "previous_retry_failure_category": str(
                    _dict(repository_test_retry_execution_result).get(
                        "failure_category"
                    )
                    or ""
                ),
                "previous_retry_failure_signal": str(
                    _dict(repository_test_retry_execution_result).get(
                        "failure_signal"
                    )
                    or ""
                ),
            }
        )
        output_paths.update(
            write_repository_test_pytest_plugin_repair_artifacts(
                repository_test_pytest_plugin_repair,
                output_root,
            )
        )
        if str(repository_test_pytest_plugin_repair.get("status") or "") == "pass":
            repository_test_pytest_plugin_repair_retry_execution_result = (
                execute_repository_test_retry_plan(
                    repository_test_retry_plan or {},
                    repository_root=effective_repository_test_root,
                    timeout=repository_test_timeout,
                    enabled=True,
                    python_executable=test_python,
                    python_executable_source=test_python_source,
                    repository_test_environment_setup_result=(
                        repository_test_environment_setup_result
                    ),
                    runner=repository_test_retry_execution_runner,
                )
            )
            repository_test_pytest_plugin_repair_retry_execution_result = dict(
                repository_test_pytest_plugin_repair_retry_execution_result
            )
            repository_test_pytest_plugin_repair_retry_execution_result.update(
                {
                    "triggered_by": "repository_test_pytest_plugin_repair",
                    "pytest_plugin_repair_applied": True,
                    "pytest_plugin_repair_status": str(
                        repository_test_pytest_plugin_repair.get("status") or ""
                    ),
                    "pytest_plugin_repair_reason": str(
                        repository_test_pytest_plugin_repair.get("reason") or ""
                    ),
                    "pytest_plugin_repair_fixture": str(
                        repository_test_pytest_plugin_repair.get("fixture") or ""
                    ),
                    "pytest_plugin_repair_plugin_requirement": str(
                        repository_test_pytest_plugin_repair.get(
                            "plugin_requirement"
                        )
                        or ""
                    ),
                    "previous_retry_failure_category": str(
                        _dict(repository_test_retry_execution_result).get(
                            "failure_category"
                        )
                        or ""
                    ),
                    "previous_retry_failure_signal": str(
                        _dict(repository_test_retry_execution_result).get(
                            "failure_signal"
                        )
                        or ""
                    ),
                }
            )
            output_paths.update(
                write_repository_test_pytest_plugin_repair_retry_execution_result_artifacts(
                    repository_test_pytest_plugin_repair_retry_execution_result,
                    output_root,
                )
            )
            effective_repository_test_retry_execution_result = (
                repository_test_pytest_plugin_repair_retry_execution_result
            )

    repository_test_timeout_narrowing = None
    if (
        run_repository_test_command
        and _should_attempt_timeout_narrowing(
            effective_repository_test_retry_execution_result
        )
    ):
        repository_test_timeout_narrowing_plan = plan_repository_test_timeout_narrowing(
            repository_test_execution_plan,
            effective_repository_test_retry_execution_result,
            repository_root=effective_repository_test_root,
        )
        repository_test_timeout_narrowing = execute_repository_test_timeout_narrowing(
            repository_test_timeout_narrowing_plan,
            enabled=True,
            timeout=repository_test_timeout,
            python_executable=test_python,
            python_executable_source=test_python_source,
            runner=repository_test_retry_execution_runner,
        )
        repository_test_timeout_narrowing = dict(repository_test_timeout_narrowing)
        repository_test_timeout_narrowing.update(
            {
                "triggered_by": "repository_test_retry_timeout",
                "previous_retry_failure_category": str(
                    _dict(effective_repository_test_retry_execution_result).get(
                        "failure_category"
                    )
                    or ""
                ),
                "previous_retry_failure_signal": str(
                    _dict(effective_repository_test_retry_execution_result).get(
                        "failure_signal"
                    )
                    or ""
                ),
            }
        )
        output_paths.update(
            write_repository_test_timeout_narrowing_artifacts(
                repository_test_timeout_narrowing,
                output_root,
            )
        )
        timeout_narrowing_selected_execution = _dict(
            repository_test_timeout_narrowing.get("selected_execution")
        )
        if timeout_narrowing_selected_execution:
            effective_repository_test_retry_execution_result = (
                timeout_narrowing_selected_execution
            )

    repository_test_dynamic_evidence = None
    if run_repository_test_command:
        repository_test_dynamic_evidence = build_repository_test_dynamic_evidence(
            repository_test_execution_result,
            effective_repository_test_retry_execution_result,
            execution_plan=repository_test_execution_plan,
            retry_plan=repository_test_retry_plan,
        )
        output_paths.update(
            write_repository_test_dynamic_evidence_artifacts(
                repository_test_dynamic_evidence,
                output_root,
            )
        )

    repository_test_failure_overlay = None
    effective_repository_test_analysis_root = effective_repository_test_root
    effective_repository_test_analysis_evidence = repository_test_dynamic_evidence
    effective_repository_test_analysis_paths: list[Any] | None = None
    if (
        run_repository_test_command
        and effective_repository_test_root is not None
        and not bool(
            _dict(repository_test_dynamic_evidence).get(
                "usable_for_localization",
                False,
            )
        )
    ):
        repository_test_failure_overlay_analysis_paths = (
            _repository_test_failure_overlay_analysis_paths(
                limited_sources_payload,
                repository_root=effective_repository_test_root,
            )
        )
        repository_test_failure_overlay = build_repository_test_failure_overlay(
            repository_root=effective_repository_test_root,
            output_dir=output_root,
            timeout=repository_test_timeout,
            candidate_limit=repository_test_failure_overlay_candidate_limit,
            analysis_paths=repository_test_failure_overlay_analysis_paths,
        )
        output_paths.update(
            write_repository_test_failure_overlay_artifacts(
                repository_test_failure_overlay,
                output_root,
            )
        )
        overlay_dynamic_evidence = _dict(
            repository_test_failure_overlay.get("dynamic_evidence")
        )
        if (
            str(repository_test_failure_overlay.get("status") or "") == "pass"
            and bool(overlay_dynamic_evidence.get("usable_for_localization", False))
        ):
            effective_repository_test_analysis_root = repository_test_failure_overlay.get(
                "overlay_root"
            )
            effective_repository_test_analysis_evidence = overlay_dynamic_evidence
            overlay_scope = _dict(repository_test_failure_overlay.get("analysis_scope"))
            effective_repository_test_analysis_paths = _list(
                overlay_scope.get("existing_files")
            )

    repository_test_fault_localization = None
    if run_repository_test_command:
        repository_test_fault_localization = build_repository_test_fault_localization(
            effective_repository_test_analysis_evidence,
            repository_root=effective_repository_test_analysis_root,
            analysis_paths=effective_repository_test_analysis_paths,
        )
        output_paths.update(
            write_repository_test_fault_localization_artifacts(
                repository_test_fault_localization,
                output_root,
            )
        )

    repository_test_patch_candidates = None
    if run_repository_test_command:
        repository_test_patch_candidates = build_repository_test_patch_candidates(
            repository_test_fault_localization,
            repository_root=effective_repository_test_analysis_root,
            analysis_paths=effective_repository_test_analysis_paths,
            candidate_limit=repository_test_failure_overlay_candidate_limit,
            patch_generation_mode=repository_patch_generation_mode,
            llm_candidate_limit=repository_llm_patch_candidate_limit,
            candidate_variant_allowlist=repository_patch_candidate_variant_allowlist,
        )
        output_paths.update(
            write_repository_test_patch_candidates_artifacts(
                repository_test_patch_candidates,
                output_root,
            )
        )

    repository_test_patch_validation = None
    if run_repository_test_command:
        repository_test_regression_validation_command = (
            _repository_test_regression_validation_command(
                repository_test_dynamic_evidence,
                repository_test_execution_plan,
            )
        )
        repository_test_patch_validation = build_repository_test_patch_validation(
            repository_test_patch_candidates,
            repository_root=effective_repository_test_analysis_root,
            validation_limit=repository_test_patch_validation_limit,
            timeout=repository_test_timeout,
            reflection_mode=repository_test_reflection_mode,
            reflection_rounds=repository_test_reflection_rounds,
            reflection_width=repository_test_reflection_width,
            patch_judge_mode=patch_judge_mode,
            regression_pytest_args=_pytest_args_from_python_module_command(
                repository_test_regression_validation_command
            ),
            regression_validation_command=repository_test_regression_validation_command,
        )
        output_paths.update(
            write_repository_test_patch_validation_artifacts(
                repository_test_patch_validation,
                output_root,
            )
        )

    repository_test_repair_summary = None
    if run_repository_test_command:
        repository_test_repair_summary = build_repository_test_repair_summary(
            repository_test_patch_validation,
            output_paths=output_paths,
            patch_candidates=repository_test_patch_candidates,
            fault_localization=repository_test_fault_localization,
            dynamic_evidence=effective_repository_test_analysis_evidence,
        )
        output_paths.update(
            write_repository_test_repair_summary_artifacts(
                repository_test_repair_summary,
                output_root,
            )
        )

    repository_test_command = None
    if run_repository_test_command:
        repository_test_command = validate_repository_test_command(
            repository_profile,
            repository_root=effective_repository_test_root,
            timeout=repository_test_timeout,
        )
        output_paths.update(
            write_repository_test_command_artifacts(
                repository_test_command,
                output_root,
            )
        )
    repository_test_setup_doctor = None
    if run_repository_test_command:
        repository_test_setup_doctor = build_repository_test_setup_doctor(
            repository_profile=repository_profile,
            repository_test_command=repository_test_command,
            repository_test_environment=repository_test_environment,
            repository_test_environment_setup=repository_test_environment_setup,
            repository_test_environment_setup_result=(
                repository_test_environment_setup_result
            ),
            repository_test_execution_plan=repository_test_execution_plan,
            repository_test_execution_result=repository_test_execution_result,
            repository_test_retry_plan=repository_test_retry_plan,
            repository_test_retry_execution_result=(
                repository_test_retry_execution_result
            ),
            repository_test_dynamic_evidence=repository_test_dynamic_evidence,
        )
        output_paths.update(
            write_repository_test_setup_doctor_artifacts(
                repository_test_setup_doctor,
                output_root,
            )
        )
    selected_candidate_count = _int(mining_payload.get("generated_count", 0))
    repository_test_manifest_evidence = _repository_test_manifest_evidence(
        natural_evidence=repository_test_dynamic_evidence,
        failure_overlay=repository_test_failure_overlay,
        fault_localization=repository_test_fault_localization,
        execution_plan=repository_test_execution_plan,
    )
    if materialize_template and selected_candidate_count > 0:
        manifest_path = BenchmarkMaterializer().materialize_template(
            template_path,
            output_root / "materialized",
            source_cache_dir=cache_root,
        )
        _annotate_manifest_with_repository_test_evidence(
            manifest_path,
            repository_test_manifest_evidence,
        )
        output_paths["materialized_manifest"] = str(manifest_path)

    benchmark_run = None
    if run_benchmark and selected_candidate_count > 0:
        benchmark_root = (
            Path(benchmark_output_dir)
            if benchmark_output_dir is not None
            else output_root / "benchmark_run"
        )
        result = run_template_benchmark(
            template_path=template_path,
            output_dir=benchmark_root,
            patch_mode=patch_mode,
            judge_mode=judge_mode,
            patch_judge_mode=patch_judge_mode,
            llm_score_mode=llm_score_mode,
            use_dynamic_coverage=use_dynamic_coverage,
            source_cache_dir=cache_root,
            repository_test_evidence=repository_test_manifest_evidence,
        )
        benchmark_run = _benchmark_result_payload(result, benchmark_root)
        output_paths["benchmark_output_dir"] = str(benchmark_root)
        output_paths["benchmark_manifest"] = benchmark_run["manifest_path"]
        output_paths["benchmark_report_json"] = benchmark_run["report_artifacts"][
            "json"
        ]
        output_paths["benchmark_report_markdown"] = benchmark_run["report_artifacts"][
            "markdown"
        ]

    selected_source_count = len(_list(limited_sources_payload.get("sources")))
    quality_summary = dict(_dict(mining_payload.get("quality_summary")))
    quality_summary.update(
        _source_limit_summary(
            _list(limited_sources_payload.get("sources")),
            imported_source_count=import_report.source_count,
            all_sources=import_report.source_entries,
            max_sources=max_sources,
            source_limit_strategy=_source_limit_strategy(
                max_sources=max_sources,
            ),
        )
    )
    quality_summary["auto_dependency_sources"] = auto_dependency_sources
    quality_summary["dependency_source_count"] = len(
        _list(dependency_sources_payload.get("sources"))
    )
    quality_summary["dependency_max_depth"] = dependency_max_depth
    quality_summary["recipe_selection_mode"] = str(recipe_selection.get("mode", ""))
    quality_summary["selected_recipes"] = [
        str(recipe) for recipe in _list(recipe_selection.get("selected_recipes"))
    ]
    quality_summary["recipe_selection_recommended_count"] = _int(
        recipe_selection.get("recommended_count", 0)
    )
    quality_summary["target_prefix"] = effective_target_prefix
    quality_summary["target_prefix_source"] = target_prefix_source
    report = GitHubBenchmarkOnboardingReport(
        mode=mode,
        preset=preset,
        source=source,
        output_dir=str(output_root),
        discovery_item_count=_discovery_item_count(effective_discovery_payload),
        imported_source_count=import_report.source_count,
        selected_source_count=selected_source_count,
        skipped_source_count=import_report.skipped_count,
        generated_candidate_count=selected_candidate_count,
        ready_for_benchmark=bool(quality_summary.get("ready_for_benchmark", False)),
        source_limit=max_sources,
        candidate_limit=max_candidates,
        requested_urls=list(requested_urls or []),
        discovery_metadata=_onboarding_discovery_metadata(
            effective_discovery_payload,
            mode=mode,
            owner=owner,
            repo=repo,
            ref=ref,
        ),
        repository_profile=repository_profile,
        quality_summary=quality_summary,
        output_paths=output_paths,
        import_report=import_report,
        mining_report=mining_report,
        benchmark_run=benchmark_run,
        repository_test_command=repository_test_command,
        repository_test_environment=repository_test_environment,
        repository_test_environment_setup=repository_test_environment_setup,
        repository_test_environment_setup_result=repository_test_environment_setup_result,
        repository_test_execution_plan=repository_test_execution_plan,
        repository_test_execution_result=repository_test_execution_result,
        repository_test_retry_plan=repository_test_retry_plan,
        repository_test_retry_execution_result=repository_test_retry_execution_result,
        repository_test_setup_doctor=repository_test_setup_doctor,
        repository_test_pytest_plugin_repair=repository_test_pytest_plugin_repair,
        repository_test_pytest_plugin_repair_retry_execution_result=(
            repository_test_pytest_plugin_repair_retry_execution_result
        ),
        repository_test_timeout_narrowing=repository_test_timeout_narrowing,
        repository_test_dynamic_evidence=repository_test_dynamic_evidence,
        repository_test_failure_overlay=repository_test_failure_overlay,
        repository_test_fault_localization=repository_test_fault_localization,
        repository_test_patch_candidates=repository_test_patch_candidates,
        repository_test_patch_validation=repository_test_patch_validation,
        repository_test_repair_summary=repository_test_repair_summary,
        repository_checkout=repository_checkout,
        repository_checkout_sources=repository_checkout_sources,
        repository_config_snapshot=repository_config_snapshot,
    )
    selection_audit = build_onboarding_selection_audit(report)
    selection_audit_paths = _write_selection_audit_artifacts(
        output_root,
        selection_audit,
    )
    report = replace(
        report,
        output_paths={**report.output_paths, **selection_audit_paths},
    )
    if run_quality_gate:
        quality_gate = evaluate_onboarding_quality_gate(
            report,
            thresholds=quality_gate_thresholds,
        )
        quality_gate_paths = _write_quality_gate_artifacts(output_root, quality_gate)
        report = replace(
            report,
            quality_gate=quality_gate.to_dict(),
            output_paths={**report.output_paths, **quality_gate_paths},
        )
    diagnostics = build_onboarding_diagnostics(report)
    diagnostics_paths = _write_diagnostics_artifacts(output_root, diagnostics)
    report = replace(
        report,
        diagnostics=diagnostics,
        benchmarkization_readiness=_dict(
            diagnostics.get("benchmarkization_readiness")
        ),
        output_paths={**report.output_paths, **diagnostics_paths},
    )
    remediation_plan_paths = _write_benchmarkization_remediation_plan_artifacts(
        output_root,
        report,
    )
    report = replace(
        report,
        output_paths={**report.output_paths, **remediation_plan_paths},
    )
    if run_showcase_lite:
        showcase_lite = build_onboarding_showcase_lite(report)
        showcase_paths = _write_showcase_lite_artifacts(output_root, showcase_lite)
        report = replace(
            report,
            showcase_lite=showcase_lite,
            output_paths={**report.output_paths, **showcase_paths},
        )
    run_config = build_onboarding_run_config(
        report,
        materialize_template=materialize_template,
        run_benchmark=run_benchmark,
        benchmark_output_dir=benchmark_output_dir,
        patch_mode=patch_mode,
        judge_mode=judge_mode,
        patch_judge_mode=patch_judge_mode,
        llm_score_mode=llm_score_mode,
        use_dynamic_coverage=use_dynamic_coverage,
        run_quality_gate=run_quality_gate,
        quality_gate_thresholds=quality_gate_thresholds
        or OnboardingQualityGateThresholds(),
        auto_dependency_sources=auto_dependency_sources,
        dependency_max_depth=dependency_max_depth,
        recipe_selection=recipe_selection,
        run_showcase_lite=run_showcase_lite,
        run_smoke_validation=run_smoke_validation,
        run_repository_test_command=run_repository_test_command,
        run_repository_test_environment_setup=run_repository_test_environment_setup,
        run_repository_test_retry=run_repository_test_retry,
        run_repository_test_retry_prerequisites=run_repository_test_retry_prerequisites,
        auto_repository_test_retry=auto_repository_test_retry,
        auto_repository_test_retry_max_risk=auto_repository_test_retry_max_risk,
        auto_repository_test_retry_allowed_runners=(
            auto_repository_test_retry_allowed_runners
        ),
        repository_test_root=repository_test_root,
        repository_test_timeout=repository_test_timeout,
        repository_test_failure_overlay_candidate_limit=repository_test_failure_overlay_candidate_limit,
        repository_test_patch_validation_limit=repository_test_patch_validation_limit,
        repository_patch_generation_mode=repository_patch_generation_mode,
        repository_llm_patch_candidate_limit=repository_llm_patch_candidate_limit,
        repository_patch_candidate_variant_allowlist=repository_patch_candidate_variant_allowlist,
        repository_test_reflection_mode=repository_test_reflection_mode,
        repository_test_reflection_rounds=repository_test_reflection_rounds,
        repository_test_reflection_width=repository_test_reflection_width,
        repository_test_environment_setup_timeout=repository_test_environment_setup_timeout,
        checkout_repository_tests=checkout_repository_tests,
        repository_checkout_timeout=repository_checkout_timeout,
        repository_checkout_depth=repository_checkout_depth,
    )
    run_config_paths = _write_run_config_artifacts(output_root, run_config)
    report = replace(
        report,
        run_config=run_config,
        output_paths={**report.output_paths, **run_config_paths},
    )
    if run_smoke_validation:
        smoke_validation, smoke_paths = _run_onboarding_smoke_validation(
            output_root,
            report,
        )
        report = replace(
            report,
            smoke_validation=smoke_validation.to_dict(),
            output_paths={**report.output_paths, **smoke_paths},
        )
        run_config = dict(report.run_config or {})
        if run_config:
            run_config["smoke_validation"] = {
                "present": True,
                "passed": smoke_validation.passed,
            }
            run_config["resolved_artifacts"] = dict(report.output_paths)
            _write_run_config_artifacts(output_root, run_config)
            report = replace(report, run_config=run_config)
        _write_onboarding_report_json(output_root, report)
    return report


def onboard_tree(
    owner: str,
    repo: str,
    ref: str | None,
    output_dir: str | Path,
    *,
    token: str | None = None,
    recursive: bool = True,
    api_base_url: str = "https://api.github.com",
    timeout: int = 20,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    preserve_paths: bool = False,
    target_prefix: str = "",
    recipes: list[str] | None = None,
    source_cache_dir: str | Path | None = None,
    max_sources: int | None = None,
    max_candidates: int | None = None,
    auto_dependency_sources: bool = True,
    dependency_max_depth: int = DEFAULT_DEPENDENCY_MAX_DEPTH,
    preset: str = "manual",
    materialize_template: bool = False,
    run_benchmark: bool = False,
    benchmark_output_dir: str | Path | None = None,
    patch_mode: str = "rule",
    judge_mode: str = "none",
    patch_judge_mode: str = "none",
    llm_score_mode: str = "none",
    use_dynamic_coverage: bool = True,
    run_quality_gate: bool = False,
    quality_gate_thresholds: OnboardingQualityGateThresholds | None = None,
    run_showcase_lite: bool = False,
    run_smoke_validation: bool = False,
    run_repository_test_command: bool = True,
    run_repository_test_environment_setup: bool = False,
    run_repository_test_retry: bool = False,
    run_repository_test_retry_prerequisites: bool = False,
    auto_repository_test_retry: bool = False,
    auto_repository_test_retry_max_risk: str = "low",
    auto_repository_test_retry_allowed_runners: list[str] | None = None,
    repository_test_root: str | Path | None = None,
    repository_test_timeout: int = 20,
    repository_test_failure_overlay_candidate_limit: int = 5,
    repository_test_patch_validation_limit: int = 5,
    repository_patch_generation_mode: str = "rule",
    repository_llm_patch_candidate_limit: int | None = None,
    repository_patch_candidate_variant_allowlist: list[str] | None = None,
    repository_test_reflection_mode: str = "rule",
    repository_test_reflection_rounds: int = 1,
    repository_test_reflection_width: int = 1,
    repository_test_environment_setup_timeout: int = 120,
    checkout_repository_tests: bool = False,
    repository_checkout_timeout: int = 120,
    repository_checkout_depth: int = 1,
    repository_checkout_runner=None,
    repository_test_environment_setup_runner=None,
    repository_test_execution_runner=None,
    repository_test_retry_execution_runner=None,
    opener=None,
) -> GitHubBenchmarkOnboardingReport:
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
    return _onboard_fetch_report(
        discovery,
        output_dir,
        source=f"github-tree:{owner}/{repo}@{resolved_ref}",
        include=include,
        exclude=exclude,
        preserve_paths=preserve_paths,
        target_prefix=target_prefix,
        recipes=recipes,
        source_cache_dir=source_cache_dir,
        max_sources=max_sources,
        max_candidates=max_candidates,
        auto_dependency_sources=auto_dependency_sources,
        dependency_max_depth=dependency_max_depth,
        preset=preset,
        materialize_template=materialize_template,
        run_benchmark=run_benchmark,
        benchmark_output_dir=benchmark_output_dir,
        patch_mode=patch_mode,
        judge_mode=judge_mode,
        patch_judge_mode=patch_judge_mode,
        llm_score_mode=llm_score_mode,
        use_dynamic_coverage=use_dynamic_coverage,
        run_quality_gate=run_quality_gate,
        quality_gate_thresholds=quality_gate_thresholds,
        run_showcase_lite=run_showcase_lite,
        run_smoke_validation=run_smoke_validation,
        run_repository_test_command=run_repository_test_command,
        run_repository_test_environment_setup=run_repository_test_environment_setup,
        run_repository_test_retry=run_repository_test_retry,
        run_repository_test_retry_prerequisites=run_repository_test_retry_prerequisites,
        auto_repository_test_retry=auto_repository_test_retry,
        auto_repository_test_retry_max_risk=auto_repository_test_retry_max_risk,
        auto_repository_test_retry_allowed_runners=(
            auto_repository_test_retry_allowed_runners
        ),
        repository_test_root=repository_test_root,
        repository_test_timeout=repository_test_timeout,
        repository_test_failure_overlay_candidate_limit=repository_test_failure_overlay_candidate_limit,
        repository_test_patch_validation_limit=repository_test_patch_validation_limit,
        repository_patch_generation_mode=repository_patch_generation_mode,
        repository_llm_patch_candidate_limit=repository_llm_patch_candidate_limit,
        repository_patch_candidate_variant_allowlist=repository_patch_candidate_variant_allowlist,
        repository_test_reflection_mode=repository_test_reflection_mode,
        repository_test_reflection_rounds=repository_test_reflection_rounds,
        repository_test_reflection_width=repository_test_reflection_width,
        repository_test_environment_setup_timeout=repository_test_environment_setup_timeout,
        checkout_repository_tests=checkout_repository_tests,
        repository_checkout_timeout=repository_checkout_timeout,
        repository_checkout_depth=repository_checkout_depth,
        repository_checkout_runner=repository_checkout_runner,
        repository_test_environment_setup_runner=repository_test_environment_setup_runner,
        repository_test_execution_runner=repository_test_execution_runner,
        repository_test_retry_execution_runner=repository_test_retry_execution_runner,
    )


def onboard_search(
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
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    preserve_paths: bool = False,
    target_prefix: str = "",
    recipes: list[str] | None = None,
    source_cache_dir: str | Path | None = None,
    max_sources: int | None = None,
    max_candidates: int | None = None,
    auto_dependency_sources: bool = True,
    dependency_max_depth: int = DEFAULT_DEPENDENCY_MAX_DEPTH,
    preset: str = "manual",
    materialize_template: bool = False,
    run_benchmark: bool = False,
    benchmark_output_dir: str | Path | None = None,
    patch_mode: str = "rule",
    judge_mode: str = "none",
    patch_judge_mode: str = "none",
    llm_score_mode: str = "none",
    use_dynamic_coverage: bool = True,
    run_quality_gate: bool = False,
    quality_gate_thresholds: OnboardingQualityGateThresholds | None = None,
    run_showcase_lite: bool = False,
    run_smoke_validation: bool = False,
    run_repository_test_command: bool = True,
    run_repository_test_environment_setup: bool = False,
    run_repository_test_retry: bool = False,
    run_repository_test_retry_prerequisites: bool = False,
    auto_repository_test_retry: bool = False,
    auto_repository_test_retry_max_risk: str = "low",
    auto_repository_test_retry_allowed_runners: list[str] | None = None,
    repository_test_root: str | Path | None = None,
    repository_test_timeout: int = 20,
    repository_test_failure_overlay_candidate_limit: int = 5,
    repository_test_patch_validation_limit: int = 5,
    repository_patch_generation_mode: str = "rule",
    repository_llm_patch_candidate_limit: int | None = None,
    repository_patch_candidate_variant_allowlist: list[str] | None = None,
    repository_test_reflection_mode: str = "rule",
    repository_test_reflection_rounds: int = 1,
    repository_test_reflection_width: int = 1,
    repository_test_environment_setup_timeout: int = 120,
    checkout_repository_tests: bool = False,
    repository_checkout_timeout: int = 120,
    repository_checkout_depth: int = 1,
    repository_checkout_runner=None,
    repository_test_environment_setup_runner=None,
    repository_test_execution_runner=None,
    repository_test_retry_execution_runner=None,
    opener=None,
) -> GitHubBenchmarkOnboardingReport:
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
    return _onboard_fetch_report(
        discovery,
        output_dir,
        source=f"github-search:{query}{scope}",
        include=include,
        exclude=exclude,
        preserve_paths=preserve_paths,
        target_prefix=target_prefix,
        recipes=recipes,
        source_cache_dir=source_cache_dir,
        max_sources=max_sources,
        max_candidates=max_candidates,
        auto_dependency_sources=auto_dependency_sources,
        dependency_max_depth=dependency_max_depth,
        preset=preset,
        materialize_template=materialize_template,
        run_benchmark=run_benchmark,
        benchmark_output_dir=benchmark_output_dir,
        patch_mode=patch_mode,
        judge_mode=judge_mode,
        patch_judge_mode=patch_judge_mode,
        llm_score_mode=llm_score_mode,
        use_dynamic_coverage=use_dynamic_coverage,
        run_quality_gate=run_quality_gate,
        quality_gate_thresholds=quality_gate_thresholds,
        run_showcase_lite=run_showcase_lite,
        run_smoke_validation=run_smoke_validation,
        run_repository_test_command=run_repository_test_command,
        run_repository_test_environment_setup=run_repository_test_environment_setup,
        run_repository_test_retry=run_repository_test_retry,
        run_repository_test_retry_prerequisites=run_repository_test_retry_prerequisites,
        auto_repository_test_retry=auto_repository_test_retry,
        auto_repository_test_retry_max_risk=auto_repository_test_retry_max_risk,
        auto_repository_test_retry_allowed_runners=(
            auto_repository_test_retry_allowed_runners
        ),
        repository_test_root=repository_test_root,
        repository_test_timeout=repository_test_timeout,
        repository_test_failure_overlay_candidate_limit=repository_test_failure_overlay_candidate_limit,
        repository_test_patch_validation_limit=repository_test_patch_validation_limit,
        repository_patch_generation_mode=repository_patch_generation_mode,
        repository_llm_patch_candidate_limit=repository_llm_patch_candidate_limit,
        repository_patch_candidate_variant_allowlist=repository_patch_candidate_variant_allowlist,
        repository_test_reflection_mode=repository_test_reflection_mode,
        repository_test_reflection_rounds=repository_test_reflection_rounds,
        repository_test_reflection_width=repository_test_reflection_width,
        repository_test_environment_setup_timeout=repository_test_environment_setup_timeout,
        checkout_repository_tests=checkout_repository_tests,
        repository_checkout_timeout=repository_checkout_timeout,
        repository_checkout_depth=repository_checkout_depth,
        repository_checkout_runner=repository_checkout_runner,
        repository_test_environment_setup_runner=repository_test_environment_setup_runner,
        repository_test_execution_runner=repository_test_execution_runner,
        repository_test_retry_execution_runner=repository_test_retry_execution_runner,
    )


def render_github_benchmark_onboarding_markdown(
    report: GitHubBenchmarkOnboardingReport,
) -> str:
    profile = _dict(report.repository_profile)
    benchmarkization = _dict(
        report.benchmarkization_readiness
    ) or _benchmarkization_readiness(report)
    lines = [
        "# GitHub Benchmark Onboarding",
        "",
        f"- Mode: `{report.mode}`",
        f"- Preset: `{report.preset}`",
        f"- Source: `{report.source}`",
        f"- Output Dir: `{report.output_dir}`",
        f"- Discovery Items: {report.discovery_item_count}",
        f"- Imported Sources: {report.imported_source_count}",
        f"- Selected Sources: {report.selected_source_count}",
        f"- Skipped Sources: {report.skipped_source_count}",
        f"- Generated Benchmark Candidates: {report.generated_candidate_count}",
        f"- Ready For Benchmark: {report.ready_for_benchmark}",
        (
            "- Quality Score: "
            f"{float(report.quality_summary.get('quality_score', 0.0)):.3f}"
        ),
        (
            "- Benchmarkization: "
            f"`{_markdown_cell(benchmarkization.get('status', 'unknown'))}` "
            f"(ready={str(bool(benchmarkization.get('ready', False))).lower()})"
        ),
        f"- Test Sources: {_int(profile.get('test_source_count', 0))}",
        (
            "- Recommended Test Command: "
            f"`{_markdown_cell(profile.get('recommended_test_command') or 'none')}`"
        ),
        (
            "- Recommended Target Prefix: "
            f"`{_markdown_cell(profile.get('recommended_target_prefix') or 'none')}`"
        ),
    ]
    if report.benchmark_run is not None:
        summary = _dict(report.benchmark_run.get("summary"))
        benchmark_repository_evidence = _dict(
            report.benchmark_run.get("repository_test_evidence")
        )
        benchmark_public_api = _dict(
            _dict(benchmark_repository_evidence.get("failure_overlay")).get(
                "public_api_evidence"
            )
        )
        lines.extend(
            [
                f"- Benchmark Cases: {_int(summary.get('case_count', 0))}",
                f"- Benchmark Top-1: {_float(summary.get('top1', 0.0)):.4f}",
                f"- Benchmark MAP: {_float(summary.get('map', 0.0)):.4f}",
                (
                    "- Benchmark Patch Success: "
                    f"{_float(summary.get('patch_success_rate', 0.0)):.4f}"
                ),
            ]
        )
        if benchmark_repository_evidence:
            lines.append(
                "- Benchmark Repository Test Evidence: "
                f"`{_markdown_cell(_format_public_api_evidence(benchmark_public_api))}`"
            )
    if report.quality_gate is not None:
        lines.append(
            f"- Quality Gate: {'PASS' if report.quality_gate.get('passed') else 'FAIL'}"
        )
    if report.diagnostics is not None:
        headline = _dict(report.diagnostics.get("headline"))
        lines.append(
            f"- Diagnostics: {_markdown_cell(headline.get('status', 'unknown'))}"
        )
    if report.showcase_lite is not None:
        lines.append("- Showcase Lite: written")
    if report.smoke_validation is not None:
        lines.append(
            "- Smoke Validation: "
            f"{'PASS' if report.smoke_validation.get('passed') else 'FAIL'}"
        )
    if report.repository_test_command is not None:
        repository_test = _dict(report.repository_test_command)
        lines.extend(
            [
                (
                    "- Repository Test Command: "
                    f"`{_markdown_cell(repository_test.get('status', ''))}` "
                    f"({ _markdown_cell(repository_test.get('reason', '')) })"
                ),
                (
                    "- Repository Test Executed: "
                    f"{str(bool(repository_test.get('executed', False))).lower()}"
                ),
            ]
        )
    if report.repository_test_environment_setup is not None:
        environment_setup = _dict(report.repository_test_environment_setup)
        lines.extend(
            [
                (
                    "- Repository Test Environment Setup: "
                    f"`{_markdown_cell(environment_setup.get('status', ''))}` "
                    f"({ _markdown_cell(environment_setup.get('reason', '')) })"
                ),
                (
                    "- Repository Test Venv Path: "
                    f"`{_markdown_cell(environment_setup.get('venv_path') or 'none')}`"
                ),
            ]
        )
    if report.repository_test_environment_setup_result is not None:
        environment_setup_result = _dict(
            report.repository_test_environment_setup_result
        )
        lines.extend(
            [
                (
                    "- Repository Test Environment Setup Result: "
                    f"`{_markdown_cell(environment_setup_result.get('status', ''))}` "
                    f"({ _markdown_cell(environment_setup_result.get('reason', '')) })"
                ),
                (
                    "- Repository Test Environment Setup Executed: "
                    f"{str(bool(environment_setup_result.get('executed', False))).lower()}"
                ),
            ]
        )
    if report.repository_test_execution_plan is not None:
        execution_plan = _dict(report.repository_test_execution_plan)
        lines.extend(
            [
                (
                    "- Repository Test Execution Plan: "
                    f"`{_markdown_cell(execution_plan.get('status', ''))}` "
                    f"({ _markdown_cell(execution_plan.get('reason', '')) })"
                ),
                (
                    "- Planned Repository Test Command: "
                    f"`{_markdown_cell(execution_plan.get('recommended_execution_command') or 'none')}`"
                ),
                (
                    "- Planned Repository Test Executable Now: "
                    f"{str(bool(execution_plan.get('executable_now', False))).lower()}"
                ),
            ]
        )
    if report.repository_test_setup_doctor is not None:
        setup_doctor = _dict(report.repository_test_setup_doctor)
        lines.extend(
            [
                (
                    "- Repository Test Setup Doctor: "
                    f"`{_markdown_cell(setup_doctor.get('status') or 'none')}`/"
                    f"`{_markdown_cell(setup_doctor.get('blocker') or 'none')}`, "
                    f"score={_float(setup_doctor.get('score', 0.0)):.4f}"
                ),
                (
                    "- Repository Test Setup Doctor Next Action: "
                    f"{_markdown_cell(setup_doctor.get('next_action') or 'none')}"
                ),
            ]
        )
    if report.repository_test_execution_result is not None:
        execution_result = _dict(report.repository_test_execution_result)
        lines.extend(
            [
                (
                    "- Planned Repository Test Result: "
                    f"`{_markdown_cell(execution_result.get('status', ''))}` "
                    f"({ _markdown_cell(execution_result.get('reason', '')) })"
                ),
                (
                    "- Planned Repository Test Executed: "
                    f"{str(bool(execution_result.get('executed', False))).lower()}"
                ),
                (
                    "- Planned Repository Test Python: "
                    f"`{_markdown_cell(execution_result.get('python_executable') or 'none')}` "
                    f"({ _markdown_cell(execution_result.get('python_executable_source') or 'none') })"
                ),
                (
                    "- Planned Repository Test Failure Category: "
                    f"`{_markdown_cell(execution_result.get('failure_category') or 'none')}`"
                ),
            ]
        )
    if report.repository_test_retry_plan is not None:
        retry_plan = _dict(report.repository_test_retry_plan)
        lines.extend(
            [
                (
                    "- Repository Test Retry Plan: "
                    f"`{_markdown_cell(retry_plan.get('status', ''))}` "
                    f"({ _markdown_cell(retry_plan.get('reason', '')) })"
                ),
                (
                    "- Repository Test Retry Recommended: "
                    f"{str(bool(retry_plan.get('retry_recommended', False))).lower()}"
                ),
                (
                    "- Repository Test Retry Strategy: "
                    f"`{_markdown_cell(retry_plan.get('retry_strategy') or 'none')}`"
                ),
                (
                    "- Repository Test Retry Command: "
                    f"`{_markdown_cell(retry_plan.get('retry_command') or 'none')}`"
                ),
            ]
        )
    if report.repository_test_retry_execution_result is not None:
        retry_result = _dict(report.repository_test_retry_execution_result)
        lines.extend(
            [
                (
                    "- Repository Test Retry Execution Result: "
                    f"`{_markdown_cell(retry_result.get('status', ''))}` "
                    f"({ _markdown_cell(retry_result.get('reason', '')) })"
                ),
                (
                    "- Repository Test Retry Executed: "
                    f"{str(bool(retry_result.get('executed', False))).lower()}"
                ),
            ]
        )
    if report.repository_test_dynamic_evidence is not None:
        dynamic_evidence = _dict(report.repository_test_dynamic_evidence)
        lines.extend(
            [
                (
                    "- Repository Test Dynamic Evidence: "
                    f"`{_markdown_cell(dynamic_evidence.get('evidence_level') or 'none')}` "
                    f"({ _markdown_cell(dynamic_evidence.get('reason', '')) })"
                ),
                (
                    "- Repository Test Dynamic Failing Tests: "
                    f"{_int(dynamic_evidence.get('failing_test_count', 0))}"
                ),
                (
                    "- Repository Test Evidence Usable For Localization: "
                    f"{str(bool(dynamic_evidence.get('usable_for_localization', False))).lower()}"
                ),
                (
                    "- Repository Test Evidence Usable For Patch Validation: "
                    f"{str(bool(dynamic_evidence.get('usable_for_patch_validation', False))).lower()}"
                ),
                (
                    "- Repository Test Evidence Usable For Regression Validation: "
                    f"{str(bool(dynamic_evidence.get('usable_for_regression_validation', False))).lower()}"
                ),
            ]
        )
    run_config_route = _repository_test_analysis_route(
        natural_evidence=_dict(report.repository_test_dynamic_evidence),
        failure_overlay=_dict(report.repository_test_failure_overlay),
        execution_plan=_dict(report.repository_test_execution_plan),
    )
    if run_config_route.get("analysis_source"):
        lines.extend(
            [
                (
                    "- Repository Test Analysis Source: "
                    f"`{_markdown_cell(run_config_route.get('analysis_source') or 'none')}`"
                ),
                (
                    "- Repository Test Overlay Trigger Reason: "
                    f"`{_markdown_cell(run_config_route.get('overlay_trigger_reason') or 'none')}`"
                ),
                (
                    "- Repository Test Phase 2 Ready: "
                    f"{str(bool(run_config_route.get('phase2_ready', False))).lower()}"
                ),
            ]
        )
    if report.repository_test_failure_overlay is not None:
        failure_overlay = _dict(report.repository_test_failure_overlay)
        selected_case = _dict(failure_overlay.get("selected_case"))
        strategy = _dict(failure_overlay.get("strategy_summary"))
        lines.extend(
            [
                (
                    "- Repository Test Failure Overlay: "
                    f"`{_markdown_cell(failure_overlay.get('status') or '')}` "
                    f"({ _markdown_cell(failure_overlay.get('reason') or '') })"
                ),
                (
                    "- Repository Test Failure Overlay Rule: "
                    f"`{_markdown_cell(selected_case.get('rule_id') or 'none')}`"
                ),
                (
                    "- Repository Test Failure Overlay Function: "
                    f"`{_markdown_cell(selected_case.get('function_name') or 'none')}`"
                ),
                (
                    "- Repository Test Failure Overlay Public API: "
                    f"`{_markdown_cell(_format_public_api_evidence(_dict(selected_case.get('public_api_evidence'))))}`"
                ),
                (
                    "- Repository Test Failure Overlay Attempts: "
                    f"{_int(failure_overlay.get('attempted_case_count', 0))}"
                ),
                (
                    "- Repository Test Failure Overlay Candidate Rules: "
                    f"`{_markdown_cell(_format_counts(_dict(strategy.get('candidate_rule_counts'))))}`"
                ),
                (
                    "- Repository Test Failure Overlay Triggered Rules: "
                    f"`{_markdown_cell(_format_counts(_dict(strategy.get('triggered_rule_counts'))))}`"
                ),
                (
                    "- Repository Test Failure Overlay Selected Rank: "
                    f"{_int(strategy.get('selected_candidate_rank', 0))}"
                ),
                (
                    "- Repository Test Failure Overlay Command: "
                    f"`{_markdown_cell(failure_overlay.get('recommended_validation_command') or 'none')}`"
                ),
            ]
        )
    if report.repository_test_fault_localization is not None:
        localization = _dict(report.repository_test_fault_localization)
        lines.extend(
            [
                (
                    "- Repository Test Fault Localization: "
                    f"`{_markdown_cell(localization.get('status') or '')}` "
                    f"({ _markdown_cell(localization.get('reason') or '') })"
                ),
                (
                    "- Repository Test Fault Localization Rankings: "
                    f"{_int(localization.get('ranking_count', 0))}"
                ),
                (
                    "- Repository Test Fault Localization Top Function: "
                    f"`{_markdown_cell(localization.get('top_function') or 'none')}`"
                ),
            ]
        )
    if report.repository_test_patch_candidates is not None:
        patch_candidates = _dict(report.repository_test_patch_candidates)
        lines.extend(
            [
                (
                    "- Repository Test Patch Candidates: "
                    f"`{_markdown_cell(patch_candidates.get('status') or '')}` "
                    f"({ _markdown_cell(patch_candidates.get('reason') or '') })"
                ),
                (
                    "- Repository Test Patch Candidate Count: "
                    f"{_int(patch_candidates.get('candidate_count', 0))}"
                ),
                (
                    "- Repository Test Patch Target Functions: "
                    f"{_int(patch_candidates.get('target_function_count', 0))}"
                ),
            ]
        )
    if report.repository_test_patch_validation is not None:
        patch_validation = _dict(report.repository_test_patch_validation)
        lines.extend(
            [
                (
                    "- Repository Test Patch Validation: "
                    f"`{_markdown_cell(patch_validation.get('status') or '')}` "
                    f"({ _markdown_cell(patch_validation.get('reason') or '') })"
                ),
                (
                    "- Repository Test Patch Validation Executed: "
                    f"{_int(patch_validation.get('executed_count', 0))}"
                ),
                (
                    "- Repository Test Patch Validation Successes: "
                    f"{_int(patch_validation.get('success_count', 0))}"
                ),
                (
                    "- Repository Test Patch Validation Reflection Successes: "
                    f"{_int(patch_validation.get('successful_reflection_candidate_count', 0))}"
                ),
                (
                    "- Repository Test Patch Validation Reflection Mode: "
                    f"`{_markdown_cell(patch_validation.get('reflection_mode') or 'none')}`"
                ),
                (
                    "- Repository Test Patch Validation Refiner: "
                    f"`{_markdown_cell(patch_validation.get('reflection_refiner_status') or 'none')}`"
                ),
                (
                    "- Repository Test Patch Validation Max Depth: "
                    f"{_int(patch_validation.get('max_depth_executed', 0))}"
                ),
                (
                    "- Repository Test Best Patch Candidate: "
                    f"`{_markdown_cell(patch_validation.get('best_candidate_id') or 'none')}`"
                ),
            ]
        )
    if report.repository_test_repair_summary is not None:
        repair_summary = _dict(report.repository_test_repair_summary)
        lines.extend(
            [
                (
                    "- Repository Test Repair Summary: "
                    f"`{_markdown_cell(repair_summary.get('status') or '')}` "
                    f"({ _markdown_cell(repair_summary.get('reason') or '') })"
                ),
                (
                    "- Repository Test Repair Ready: "
                    f"{str(bool(repair_summary.get('repair_ready', False))).lower()}"
                ),
                (
                    "- Repository Test Repair Scope: "
                    f"`{_markdown_cell(repair_summary.get('repair_validation_scope') or 'none')}`"
                ),
                (
                    "- Repository Test Repair Patch: "
                    f"`{_markdown_cell(repair_summary.get('patch_path') or 'none')}`"
                ),
            ]
        )
    if report.source_limit is not None or report.candidate_limit is not None:
        lines.extend(
            [
                f"- Source Limit: {report.source_limit or 'none'}",
                (
                    "- Source Limit Strategy: "
                    f"{report.quality_summary.get('source_limit_strategy', 'all')}"
                ),
                f"- Candidate Limit: {report.candidate_limit or 'none'}",
                (
                    "- Candidate Limit Strategy: "
                    f"{report.quality_summary.get('candidate_limit_strategy', 'all')}"
                ),
                (
                    "- Source Coverage: "
                    f"groups={_format_ratio(report.quality_summary.get('source_group_coverage'))}, "
                    f"directories={_format_ratio(report.quality_summary.get('source_directory_coverage'))}, "
                    f"omitted={_int(report.quality_summary.get('omitted_source_count', 0))}"
                ),
                (
                    "- Candidate Coverage: "
                    f"rules={_format_ratio(report.quality_summary.get('candidate_rule_coverage'))}, "
                    f"bug_types={_format_ratio(report.quality_summary.get('candidate_bug_type_coverage'))}, "
                    f"sources={_format_ratio(report.quality_summary.get('candidate_source_coverage'))}, "
                    f"omitted={_int(report.quality_summary.get('omitted_candidate_count', 0))}"
                ),
            ]
        )
    lines.extend(
        [
            "",
            "## Benchmarkization Readiness",
            "",
            f"- Status: `{_markdown_cell(benchmarkization.get('status', 'unknown'))}`",
            f"- Stage: `{_markdown_cell(benchmarkization.get('stage', 'unknown'))}`",
            f"- Ready: {str(bool(benchmarkization.get('ready', False))).lower()}",
            (
                "- Repository Test Evidence: "
                f"`{_markdown_cell(benchmarkization.get('repository_test_evidence_status') or 'not_started')}`"
            ),
            f"- Benchmark Cases: {_int(benchmarkization.get('benchmark_cases', 0))}",
        ]
    )
    blocking_reasons = [
        str(reason) for reason in _list(benchmarkization.get("blocking_reasons"))
    ]
    lines.append(
        "- Blocking Reasons: "
        f"`{_markdown_cell(', '.join(blocking_reasons) or 'none')}`"
    )
    next_actions = _list(benchmarkization.get("next_actions"))
    if next_actions:
        lines.extend(["", "Next actions:"])
        for action in next_actions:
            lines.append(f"- {_markdown_cell(action)}")
    remediation_plan = _dict(benchmarkization.get("remediation_plan"))
    remediation_actions = _list(remediation_plan.get("actions"))
    if remediation_actions:
        lines.extend(
            [
                "",
                "Remediation plan:",
                "",
                "| Action | Stage | Auto | Risk | Command | Expected Outcome |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for action_value in remediation_actions:
            action = _dict(action_value)
            lines.append(
                "| "
                f"{_markdown_cell(action.get('action_id', ''))} | "
                f"{_markdown_cell(action.get('stage', ''))} | "
                f"{str(bool(action.get('auto_runnable', False))).lower()} | "
                f"{_markdown_cell(action.get('risk', ''))} | "
                f"`{_markdown_cell(action.get('command') or 'none')}` | "
                f"{_markdown_cell(action.get('expected_outcome', ''))} |"
            )
    lines.extend(["", "| Artifact | Path |", "| --- | --- |"])
    for name, path in report.output_paths.items():
        lines.append(f"| {_markdown_cell(name)} | `{_markdown_cell(path)}` |")
    if report.requested_urls:
        lines.extend(["", "## Requested URLs", "", "| URL |", "| --- |"])
        for url in report.requested_urls:
            lines.append(f"| {_markdown_cell(url)} |")
    if report.benchmark_run is None and report.generated_candidate_count > 0:
        lines.extend(
            [
                "",
                "## Next Step",
                "",
                "Materialize and evaluate the generated template:",
                "",
                "```bash",
                "python -m code_intelligence_agent.evaluation.run_template_benchmark "
                f"{report.output_paths['template']} "
                f"{Path(report.output_dir) / 'benchmark_run'} "
                "--format markdown "
                f"--source-cache-dir {report.output_paths['source_cache_dir']}",
                "```",
            ]
        )
    return "\n".join(lines)


def _first_non_empty_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        item = _dict(value)
        if item:
            return item
    return {}


def _repository_test_evidence_readiness(
    report: GitHubBenchmarkOnboardingReport,
) -> dict[str, Any]:
    benchmark_evidence = _dict(
        _dict(report.benchmark_run).get("repository_test_evidence")
    )
    benchmark_overlay = _dict(benchmark_evidence.get("failure_overlay"))
    benchmark_localization = _dict(benchmark_evidence.get("fault_localization"))

    runtime_overlay = _dict(report.repository_test_failure_overlay)
    runtime_selected_case = _dict(runtime_overlay.get("selected_case"))
    runtime_dynamic = _dict(runtime_overlay.get("dynamic_evidence"))
    runtime_localization = _dict(report.repository_test_fault_localization)

    benchmark_public_api = _first_non_empty_dict(
        benchmark_overlay.get("public_api_evidence"),
        benchmark_localization.get("public_api_evidence"),
    )
    runtime_public_api = _first_non_empty_dict(
        runtime_overlay.get("public_api_evidence"),
        runtime_selected_case.get("public_api_evidence"),
        runtime_dynamic.get("public_api_evidence"),
        runtime_localization.get("public_api_evidence"),
    )
    public_api = benchmark_public_api or runtime_public_api

    benchmark_context = _first_non_empty_dict(
        benchmark_overlay.get("overlay_case_context"),
        benchmark_localization.get("overlay_case_context"),
    )
    runtime_context = _first_non_empty_dict(
        runtime_overlay.get("overlay_case_context"),
        runtime_selected_case.get("overlay_case_context"),
        runtime_dynamic.get("overlay_case_context"),
        runtime_localization.get("overlay_case_context"),
    )
    overlay_case_context = benchmark_context or runtime_context

    repository_test_started = any(
        bool(item)
        for item in [
            runtime_public_api,
            runtime_context,
            runtime_dynamic
            if str(runtime_dynamic.get("evidence_level") or "") not in {"", "none"}
            else {},
            runtime_overlay
            if str(runtime_overlay.get("status") or "") not in {"", "skipped"}
            else {},
            runtime_localization
            if str(runtime_localization.get("status") or "") not in {"", "skipped"}
            else {},
            _dict(report.repository_test_patch_candidates)
            if str(
                _dict(report.repository_test_patch_candidates).get("status") or ""
            )
            not in {"", "skipped"}
            else {},
            _dict(report.repository_test_patch_validation)
            if str(
                _dict(report.repository_test_patch_validation).get("status") or ""
            )
            not in {"", "skipped"}
            else {},
        ]
    )
    benchmark_chain_present = bool(
        benchmark_evidence and benchmark_public_api and benchmark_context
    )
    runtime_chain_present = bool(
        (runtime_overlay or runtime_localization)
        and runtime_public_api
        and runtime_context
    )
    if benchmark_chain_present:
        status = "benchmark_ready"
        evidence_source = "benchmark_run"
    elif runtime_chain_present:
        status = "runtime_ready"
        evidence_source = "runtime_report"
    elif repository_test_started:
        status = "missing_public_api_evidence"
        evidence_source = "repository_test_started"
    else:
        status = "not_started"
        evidence_source = "none"

    analysis_route = _dict(benchmark_evidence.get("analysis_route"))
    return {
        "status": status,
        "evidence_source": evidence_source,
        "repository_test_started": repository_test_started,
        "benchmark_repository_test_evidence_present": bool(benchmark_evidence),
        "runtime_evidence_chain_present": runtime_chain_present,
        "benchmark_evidence_chain_present": benchmark_chain_present,
        "public_api_trace_present": bool(public_api),
        "overlay_case_context_present": bool(overlay_case_context),
        "analysis_source": str(analysis_route.get("analysis_source") or ""),
        "phase2_ready": bool(analysis_route.get("phase2_ready", False)),
        "trigger_scope": str(public_api.get("trigger_scope") or ""),
        "trigger_expression": str(public_api.get("trigger_expression") or ""),
        "public_entrypoint": str(public_api.get("public_entrypoint") or ""),
        "internal_target": str(public_api.get("internal_target") or ""),
        "selected_rule_id": str(
            benchmark_overlay.get("selected_rule_id")
            or runtime_selected_case.get("rule_id")
            or ""
        ),
        "selected_function": str(
            benchmark_overlay.get("selected_function")
            or runtime_selected_case.get("function_name")
            or ""
        ),
        "top_function": str(
            benchmark_localization.get("top_function")
            or runtime_localization.get("top_function")
            or ""
        ),
    }


def _readiness_check(
    name: str,
    passed: bool,
    *,
    expected: str,
    actual: Any,
    stage: str,
    blocking: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "expected": expected,
        "actual": str(actual),
        "stage": stage,
        "blocking": bool(blocking),
    }


def _benchmarkization_next_actions(
    *,
    status: str,
    report: GitHubBenchmarkOnboardingReport,
) -> list[str]:
    plan = _benchmarkization_remediation_plan(status=status, report=report)
    actions = []
    for action in _list(plan.get("actions")):
        action_dict = _dict(action)
        description = str(action_dict.get("description") or "")
        command = str(action_dict.get("command") or "")
        if description:
            actions.append(description)
        if command:
            actions.append(command)
    return actions or ["Inspect onboarding_diagnostics.md for the first failing stage."]


def _remediation_action(
    action_id: str,
    *,
    stage: str,
    description: str,
    expected_outcome: str,
    auto_runnable: bool = False,
    command: str = "",
    risk: str = "low",
    requires: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "stage": stage,
        "description": description,
        "auto_runnable": bool(auto_runnable),
        "command": command,
        "risk": risk,
        "requires": list(requires or []),
        "expected_outcome": expected_outcome,
    }


def _benchmarkization_remediation_plan(
    *,
    status: str,
    report: GitHubBenchmarkOnboardingReport,
) -> dict[str, Any]:
    template_path = report.output_paths.get("template", "source_mining_template.json")
    source_cache_dir = report.output_paths.get(
        "source_cache_dir",
        "github_raw_source_cache",
    )
    benchmark_dir = Path(report.output_dir) / "benchmark_run"
    run_benchmark_command = (
        "python -m code_intelligence_agent.evaluation.run_template_benchmark "
        f"{template_path} {benchmark_dir} --format markdown "
        f"--source-cache-dir {source_cache_dir}"
    )
    if status == "blocked_at_source_import":
        actions = [
            _remediation_action(
                "check_github_access",
                stage="source_import",
                description=(
                    "Check the repository URL, ref, include/exclude filters, and GitHub API access."
                ),
                expected_outcome="Discovery and source import produce at least one Python source.",
                requires=["repository_url_or_discovery_payload"],
            ),
            _remediation_action(
                "rerun_with_github_token_or_pinned_ref",
                stage="source_import",
                description=(
                    "For private or rate-limited repositories, set GITHUB_TOKEN or rerun with a pinned ref."
                ),
                expected_outcome="Raw source fetching succeeds without rate-limit or access errors.",
                requires=["github_token_or_valid_ref"],
            ),
        ]
    elif status == "blocked_at_candidate_generation":
        actions = [
            _remediation_action(
                "inspect_recipe_misses",
                stage="source_mining",
                description="Inspect source_mining.md for recipe miss reasons.",
                expected_outcome="Identify whether filters, recipes, or source limits blocked candidate generation.",
                requires=["source_mining_markdown"],
            ),
            _remediation_action(
                "broaden_recipe_mining",
                stage="source_mining",
                description=(
                    "Run without a narrow --recipe filter or increase --max-sources to broaden mining."
                ),
                expected_outcome="Generate at least one benchmark candidate.",
                requires=["source_cache_dir"],
            ),
        ]
    elif status == "ready_to_run_benchmark":
        actions = [
            _remediation_action(
                "run_template_benchmark",
                stage="benchmark",
                description="Materialize and execute the generated template with run_template_benchmark.",
                auto_runnable=True,
                command=run_benchmark_command,
                expected_outcome="Benchmark report is written and benchmarkization advances to benchmark_ready.",
                requires=["source_mining_template", "source_cache_dir"],
            )
        ]
    elif status == "benchmark_artifacts_invalid":
        actions = [
            _remediation_action(
                "inspect_benchmark_validation",
                stage="benchmark",
                description="Inspect benchmark_run/benchmark_report.md and manifest validation errors.",
                expected_outcome="Identify invalid template or manifest fields.",
                requires=["benchmark_report"],
            ),
            _remediation_action(
                "regenerate_template_after_metadata_fix",
                stage="benchmark",
                description="Regenerate the template from onboarding after fixing invalid case metadata.",
                expected_outcome="Template and manifest validation pass.",
                requires=["source_mining_template"],
            ),
        ]
    elif status == "quality_gate_failed":
        actions = [
            _remediation_action(
                "inspect_quality_gate",
                stage="quality_gate",
                description="Inspect onboarding_quality_gate.md for failed thresholds.",
                expected_outcome="Find the specific failed coverage or metric threshold.",
                requires=["quality_gate_markdown"],
            ),
            _remediation_action(
                "improve_or_relax_quality_thresholds",
                stage="quality_gate",
                description=(
                    "Improve source/candidate coverage or use exploratory thresholds for early runs."
                ),
                expected_outcome="Quality gate passes for the intended evaluation mode.",
                requires=["quality_gate_thresholds"],
            ),
        ]
    elif status == "repository_test_evidence_incomplete":
        actions = [
            _remediation_action(
                "inspect_repository_test_evidence",
                stage="repository_test",
                description=(
                    "Inspect repository_test_failure_overlay.md and repository_test_fault_localization.md."
                ),
                expected_outcome="Identify missing public API trigger or overlay context.",
                requires=["repository_test_artifacts"],
            ),
            _remediation_action(
                "generate_public_api_trace",
                stage="repository_test",
                description=(
                    "Generate a public API trigger trace before treating repository tests as localization evidence."
                ),
                expected_outcome="Repository test evidence status becomes runtime_ready or benchmark_ready.",
                requires=["repository_test_root"],
            ),
        ]
    elif status == "benchmark_ready":
        actions = [
            _remediation_action(
                "publish_benchmark_evidence_bundle",
                stage="complete",
                description=(
                    "Use onboarding_report.md, benchmark_report.md, and onboarding_run_config.md as the benchmark evidence bundle."
                ),
                expected_outcome="The benchmark can be reviewed or attached to a project report.",
                requires=["onboarding_report", "benchmark_report", "run_config"],
            ),
            _remediation_action(
                "scale_to_more_repositories",
                stage="complete",
                description=(
                    "Scale the same onboarding command to more repositories or add the case to the evaluation suite."
                ),
                expected_outcome="Broader benchmark coverage across GitHub repositories.",
                requires=["validated_onboarding_command"],
            ),
        ]
    else:
        actions = [
            _remediation_action(
                "inspect_diagnostics",
                stage="diagnostics",
                description="Inspect onboarding_diagnostics.md for the first failing stage.",
                expected_outcome="Determine the next actionable onboarding fix.",
                requires=["diagnostics_markdown"],
            )
        ]
    return {
        "status": status,
        "primary_action_id": str(_dict(actions[0]).get("action_id") or "")
        if actions
        else "",
        "auto_runnable_action_count": sum(
            1 for action in actions if _dict(action).get("auto_runnable", False)
        ),
        "manual_action_count": sum(
            1 for action in actions if not _dict(action).get("auto_runnable", False)
        ),
        "actions": actions,
    }


def build_benchmarkization_remediation_plan_artifact(
    report: GitHubBenchmarkOnboardingReport,
) -> dict[str, Any]:
    readiness = _dict(report.benchmarkization_readiness) or _benchmarkization_readiness(
        report
    )
    plan = _dict(readiness.get("remediation_plan")) or _benchmarkization_remediation_plan(
        status=str(readiness.get("status") or ""),
        report=report,
    )
    actions = [_dict(action) for action in _list(plan.get("actions"))]
    auto_runnable_actions = [
        action for action in actions if bool(action.get("auto_runnable", False))
    ]
    manual_actions = [
        action for action in actions if not bool(action.get("auto_runnable", False))
    ]
    primary_action = actions[0] if actions else {}
    return {
        "kind": "benchmarkization_remediation_plan",
        "status": str(readiness.get("status") or ""),
        "stage": str(readiness.get("stage") or ""),
        "ready": bool(readiness.get("ready", False)),
        "repository_test_evidence_status": str(
            readiness.get("repository_test_evidence_status") or "not_started"
        ),
        "benchmark_run_present": bool(
            readiness.get("benchmark_run_present", False)
        ),
        "benchmark_cases": _int(readiness.get("benchmark_cases", 0)),
        "blocking_reasons": [
            str(reason) for reason in _list(readiness.get("blocking_reasons"))
        ],
        "next_actions": [
            str(action) for action in _list(readiness.get("next_actions"))
        ],
        "primary_action_id": str(
            plan.get("primary_action_id")
            or primary_action.get("action_id")
            or ""
        ),
        "primary_command": str(primary_action.get("command") or ""),
        "auto_runnable_action_count": len(auto_runnable_actions),
        "manual_action_count": len(manual_actions),
        "action_count": len(actions),
        "actions": actions,
        "output_dir": report.output_dir,
        "source": report.source,
        "mode": report.mode,
        "preset": report.preset,
        "artifacts": {
            key: value
            for key, value in report.output_paths.items()
            if key
            in {
                "template",
                "source_mining_markdown",
                "source_import_markdown",
                "diagnostics_markdown",
                "run_config_markdown",
                "quality_gate_markdown",
                "benchmark_report_markdown",
                "source_cache_dir",
            }
        },
    }


def render_benchmarkization_remediation_plan_markdown(
    plan: dict[str, Any],
) -> str:
    lines = [
        "# Benchmarkization Remediation Plan",
        "",
        f"- Status: `{_markdown_cell(plan.get('status', 'unknown'))}`",
        f"- Stage: `{_markdown_cell(plan.get('stage', 'unknown'))}`",
        f"- Ready: {str(bool(plan.get('ready', False))).lower()}",
        (
            "- Repository Test Evidence: "
            f"`{_markdown_cell(plan.get('repository_test_evidence_status') or 'not_started')}`"
        ),
        f"- Benchmark Run Present: {str(bool(plan.get('benchmark_run_present', False))).lower()}",
        f"- Benchmark Cases: {_int(plan.get('benchmark_cases', 0))}",
        (
            "- Blocking Reasons: "
            f"`{_markdown_cell(', '.join(str(item) for item in _list(plan.get('blocking_reasons'))) or 'none')}`"
        ),
        (
            "- Primary Action: "
            f"`{_markdown_cell(plan.get('primary_action_id') or 'none')}`"
        ),
        (
            "- Primary Command: "
            f"`{_markdown_cell(plan.get('primary_command') or 'none')}`"
        ),
        f"- Auto-Runnable Actions: {_int(plan.get('auto_runnable_action_count', 0))}",
        f"- Manual Actions: {_int(plan.get('manual_action_count', 0))}",
        "",
        "## Actions",
        "",
        "| Action | Stage | Auto | Risk | Requires | Command | Expected Outcome |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for action_value in _list(plan.get("actions")):
        action = _dict(action_value)
        lines.append(
            "| "
            f"{_markdown_cell(action.get('action_id', ''))} | "
            f"{_markdown_cell(action.get('stage', ''))} | "
            f"{str(bool(action.get('auto_runnable', False))).lower()} | "
            f"{_markdown_cell(action.get('risk', ''))} | "
            f"{_markdown_cell(', '.join(str(item) for item in _list(action.get('requires'))) or 'none')} | "
            f"`{_markdown_cell(action.get('command') or 'none')}` | "
            f"{_markdown_cell(action.get('expected_outcome', ''))} |"
        )
    if not _list(plan.get("actions")):
        lines.append("| none | none | false | none | none | `none` | none |")
    next_actions = [str(action) for action in _list(plan.get("next_actions"))]
    lines.extend(["", "## Next Actions", ""])
    if next_actions:
        for action in next_actions:
            lines.append(f"- {_markdown_cell(action)}")
    else:
        lines.append("- none")
    artifacts = _dict(plan.get("artifacts"))
    lines.extend(["", "## Artifacts", "", "| Artifact | Path |", "| --- | --- |"])
    if artifacts:
        for key, value in sorted(artifacts.items()):
            lines.append(f"| {_markdown_cell(key)} | `{_markdown_cell(value)}` |")
    else:
        lines.append("| none | `none` |")
    return "\n".join(lines)


def _benchmarkization_readiness(
    report: GitHubBenchmarkOnboardingReport,
    *,
    diagnostic_issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    benchmark = _dict(report.benchmark_run)
    benchmark_summary = _dict(benchmark.get("summary"))
    template_validation = _dict(benchmark.get("template_validation"))
    manifest_validation = _dict(benchmark.get("manifest_validation"))
    quality_gate = _dict(report.quality_gate)
    repository_test_readiness = _repository_test_evidence_readiness(report)

    benchmark_present = bool(benchmark)
    benchmark_cases = _int(benchmark_summary.get("case_count", 0))
    template_valid = bool(template_validation.get("is_valid", False))
    manifest_valid = bool(manifest_validation.get("is_valid", False))
    quality_gate_passed = quality_gate.get("passed")
    repository_test_started = bool(
        repository_test_readiness.get("repository_test_started", False)
    )
    repository_test_evidence_ready = (
        not repository_test_started
        or repository_test_readiness.get("status")
        in {"runtime_ready", "benchmark_ready"}
    )

    checks = [
        _readiness_check(
            "discovery_items",
            report.discovery_item_count > 0,
            expected="> 0",
            actual=report.discovery_item_count,
            stage="discovery",
        ),
        _readiness_check(
            "imported_sources",
            report.imported_source_count > 0,
            expected="> 0",
            actual=report.imported_source_count,
            stage="source_import",
        ),
        _readiness_check(
            "selected_sources",
            report.selected_source_count > 0,
            expected="> 0",
            actual=report.selected_source_count,
            stage="source_selection",
        ),
        _readiness_check(
            "generated_candidates",
            report.generated_candidate_count > 0,
            expected="> 0",
            actual=report.generated_candidate_count,
            stage="source_mining",
        ),
        _readiness_check(
            "source_mining_ready_for_benchmark",
            report.ready_for_benchmark,
            expected="true",
            actual=str(report.ready_for_benchmark).lower(),
            stage="source_mining",
            blocking=False,
        ),
        _readiness_check(
            "benchmark_run_present",
            benchmark_present,
            expected="true",
            actual=str(benchmark_present).lower(),
            stage="benchmark",
        ),
    ]
    if benchmark_present:
        checks.extend(
            [
                _readiness_check(
                    "benchmark_template_validation",
                    template_valid,
                    expected="valid",
                    actual=str(template_valid).lower(),
                    stage="benchmark",
                ),
                _readiness_check(
                    "benchmark_manifest_validation",
                    manifest_valid,
                    expected="valid",
                    actual=str(manifest_valid).lower(),
                    stage="benchmark",
                ),
                _readiness_check(
                    "benchmark_cases",
                    benchmark_cases > 0,
                    expected="> 0",
                    actual=benchmark_cases,
                    stage="benchmark",
                ),
            ]
        )
    if quality_gate:
        checks.append(
            _readiness_check(
                "quality_gate_passed",
                bool(quality_gate_passed),
                expected="true",
                actual=str(quality_gate_passed).lower(),
                stage="quality_gate",
            )
        )
    if repository_test_started:
        checks.append(
            _readiness_check(
                "repository_test_evidence_chain",
                repository_test_evidence_ready,
                expected="runtime_ready or benchmark_ready",
                actual=str(repository_test_readiness.get("status") or "missing"),
                stage="repository_test",
            )
        )

    if report.discovery_item_count <= 0 or report.imported_source_count <= 0:
        status = "blocked_at_source_import"
        stage = "source_import"
    elif report.selected_source_count <= 0 or report.generated_candidate_count <= 0:
        status = "blocked_at_candidate_generation"
        stage = "source_mining"
    elif not benchmark_present:
        status = "ready_to_run_benchmark"
        stage = "benchmark"
    elif not template_valid or not manifest_valid or benchmark_cases <= 0:
        status = "benchmark_artifacts_invalid"
        stage = "benchmark"
    elif quality_gate and not bool(quality_gate_passed):
        status = "quality_gate_failed"
        stage = "quality_gate"
    elif not repository_test_evidence_ready:
        status = "repository_test_evidence_incomplete"
        stage = "repository_test"
    else:
        status = "benchmark_ready"
        stage = "complete"

    blocking_reasons = [
        str(check.get("name"))
        for check in checks
        if check.get("blocking", True) and not check.get("passed", False)
    ]
    issues = diagnostic_issues
    if issues is None:
        issues = _list(_dict(report.diagnostics).get("issues"))
    diagnostic_error_count = sum(
        1 for issue in issues if _dict(issue).get("severity") == "error"
    )
    remediation_plan = _benchmarkization_remediation_plan(
        status=status,
        report=report,
    )
    return {
        "status": status,
        "stage": stage,
        "ready": status == "benchmark_ready",
        "blocking_reasons": blocking_reasons,
        "next_actions": _benchmarkization_next_actions(
            status=status,
            report=report,
        ),
        "remediation_plan": remediation_plan,
        "diagnostic_error_count": diagnostic_error_count,
        "benchmark_run_present": benchmark_present,
        "benchmark_cases": benchmark_cases,
        "quality_gate_present": bool(quality_gate),
        "quality_gate_passed": quality_gate_passed if quality_gate else None,
        "repository_test_evidence_status": repository_test_readiness.get("status"),
        "repository_test_evidence_ready": repository_test_evidence_ready,
        "checks": checks,
    }


def build_onboarding_diagnostics(
    report: GitHubBenchmarkOnboardingReport,
) -> dict[str, Any]:
    repository_test_readiness = _repository_test_evidence_readiness(report)
    import_skips = _import_skip_diagnostics(report)
    source_read_errors = _source_read_error_diagnostics(report)
    recipe_misses = _recipe_miss_diagnostics(report)
    recipe_suggestions = _recipe_suggestion_diagnostics(recipe_misses)
    gate_failures = _quality_gate_failure_diagnostics(report)
    issues: list[dict[str, Any]] = []

    if report.discovery_item_count <= 0:
        issues.append(
            _diagnostic_issue(
                stage="discovery",
                severity="error",
                code="no_discovery_items",
                message="Discovery did not return any files to evaluate.",
                count=0,
                examples=[],
                next_steps=[
                    "Check the repository URL, ref, include filters, or GitHub API access.",
                    "For a pinned run, pass a commit SHA with --ref.",
                ],
            )
        )
    if report.imported_source_count <= 0:
        issues.append(
            _diagnostic_issue(
                stage="source_import",
                severity="error",
                code="no_imported_sources",
                message="No Python sources were imported from discovery results.",
                count=0,
                examples=import_skips[:10],
                next_steps=[
                    "Inspect source_import.md for skipped paths and reasons.",
                    "Relax --include/--exclude filters or target a repository with Python source files.",
                ],
            )
        )
    elif import_skips:
        issues.append(
            _diagnostic_issue(
                stage="source_import",
                severity="warning",
                code="skipped_sources",
                message="Some discovered files were skipped before recipe mining.",
                count=len(import_skips),
                examples=import_skips[:10],
                next_steps=[
                    "Inspect source_import.md to decide whether skipped files are expected.",
                    "Use --include/--exclude or --preserve-paths if the selected source surface is too narrow.",
                ],
            )
        )
    if report.selected_source_count <= 0:
        issues.append(
            _diagnostic_issue(
                stage="source_selection",
                severity="error",
                code="no_selected_sources",
                message="No imported source survived source limiting.",
                count=0,
                examples=[],
                next_steps=[
                    "Increase --max-sources or remove source filters.",
                    "Inspect onboarding_selection_audit.md for selection details.",
                ],
            )
        )
    if source_read_errors:
        issues.append(
            _diagnostic_issue(
                stage="source_mining",
                severity="error" if report.generated_candidate_count <= 0 else "warning",
                code="source_read_errors",
                message="One or more selected sources could not be read during recipe mining.",
                count=len(source_read_errors),
                examples=source_read_errors[:10],
                next_steps=[
                    "Check network access and GitHub raw URL availability.",
                    "Provide --source-cache-dir with pre-fetched raw sources when running offline.",
                    "If the repository is private or rate-limited, set GITHUB_TOKEN.",
                ],
            )
        )
    if report.selected_source_count > 0 and report.generated_candidate_count <= 0:
        issues.append(
            _diagnostic_issue(
                stage="source_mining",
                severity="error",
                code="no_generated_candidates",
                message="Recipe mining completed without generating benchmark candidates.",
                count=0,
                examples=recipe_misses[:10],
                next_steps=[
                    "Run without --recipe or add more recipe families to broaden matching.",
                    "Increase --max-sources so mining sees more files and directories.",
                    "Inspect source_mining.md for per-source recipe miss reasons.",
                ],
            )
        )
    elif recipe_misses:
        issues.append(
            _diagnostic_issue(
                stage="source_mining",
                severity="info",
                code="recipe_misses",
                message="Some source x recipe combinations did not match a supported mutation pattern.",
                count=len(recipe_misses),
                examples=recipe_misses[:10],
                next_steps=[
                    "This is normal for broad GitHub repositories.",
                    "Use onboarding_selection_audit.md to confirm generated candidates remain representative.",
                ],
            )
        )
    if gate_failures:
        issues.append(
            _diagnostic_issue(
                stage="quality_gate",
                severity="error",
                code="quality_gate_failed",
                message="The onboarding quality gate failed one or more checks.",
                count=len(gate_failures),
                examples=gate_failures[:10],
                next_steps=[
                    "Inspect onboarding_quality_gate.md for expected and actual values.",
                    "Improve source/candidate coverage or lower exploratory thresholds for early mining runs.",
                ],
            )
        )
    if report.generated_candidate_count > 0 and report.benchmark_run is None:
        issues.append(
            _diagnostic_issue(
                stage="benchmark",
                severity="info",
                code="benchmark_not_run",
                message="Benchmark candidates were generated but not executed yet.",
                count=report.generated_candidate_count,
                examples=[],
                next_steps=[
                    "Run with --preset smoke or --run-benchmark to validate generated candidates.",
                    "Use the Next Step command in onboarding_report.md.",
                ],
            )
        )

    status = _diagnostic_status(issues)
    benchmarkization_readiness = _benchmarkization_readiness(
        report,
        diagnostic_issues=issues,
    )
    return {
        "headline": {
            "status": status,
            "issue_count": len(issues),
            "error_count": sum(1 for issue in issues if issue.get("severity") == "error"),
            "warning_count": sum(
                1 for issue in issues if issue.get("severity") == "warning"
            ),
            "info_count": sum(1 for issue in issues if issue.get("severity") == "info"),
            "first_failing_stage": _first(
                [
                    str(issue.get("stage", ""))
                    for issue in issues
                    if issue.get("severity") == "error"
                ]
            ),
        },
        "summary": {
            "mode": report.mode,
            "preset": report.preset,
            "source": report.source,
            "discovery_items": report.discovery_item_count,
            "imported_sources": report.imported_source_count,
            "selected_sources": report.selected_source_count,
            "skipped_sources": report.skipped_source_count,
            "generated_candidates": report.generated_candidate_count,
            "ready_for_benchmark": report.ready_for_benchmark,
            "quality_score": _float(report.quality_summary.get("quality_score", 0.0)),
            "benchmarkization_status": benchmarkization_readiness["status"],
            "benchmarkization_ready": benchmarkization_readiness["ready"],
            "benchmark_run_present": report.benchmark_run is not None,
            "repository_test_evidence_status": repository_test_readiness["status"],
            "repository_test_runtime_evidence_chain_present": (
                repository_test_readiness["runtime_evidence_chain_present"]
            ),
            "repository_test_benchmark_evidence_chain_present": (
                repository_test_readiness["benchmark_evidence_chain_present"]
            ),
            "repository_test_public_api_trace_present": (
                repository_test_readiness["public_api_trace_present"]
            ),
            "quality_gate_present": report.quality_gate is not None,
            "quality_gate_passed": _dict(report.quality_gate).get("passed")
            if report.quality_gate is not None
            else None,
        },
        "benchmarkization_readiness": benchmarkization_readiness,
        "repository_test_readiness": repository_test_readiness,
        "issues": issues,
        "source_read_errors": source_read_errors,
        "recipe_misses": recipe_misses,
        "recipe_suggestions": recipe_suggestions,
        "quality_gate_failures": gate_failures,
        "next_actions": _diagnostic_next_actions(issues),
    }


def render_onboarding_diagnostics_markdown(diagnostics: dict[str, Any]) -> str:
    headline = _dict(diagnostics.get("headline"))
    summary = _dict(diagnostics.get("summary"))
    issues = _list(diagnostics.get("issues"))
    lines = [
        "# GitHub Onboarding Diagnostics",
        "",
        "## Summary",
        "",
        f"- Status: `{headline.get('status', 'unknown')}`",
        f"- Source: `{summary.get('source', '')}`",
        f"- Mode: `{summary.get('mode', '')}`",
        f"- Preset: `{summary.get('preset', 'manual')}`",
        f"- Discovery Items: {_int(summary.get('discovery_items', 0))}",
        f"- Imported Sources: {_int(summary.get('imported_sources', 0))}",
        f"- Selected Sources: {_int(summary.get('selected_sources', 0))}",
        f"- Generated Candidates: {_int(summary.get('generated_candidates', 0))}",
        f"- Quality Score: {_float(summary.get('quality_score', 0.0)):.3f}",
        f"- Ready For Benchmark: {summary.get('ready_for_benchmark', False)}",
        f"- Benchmarkization: `{summary.get('benchmarkization_status', 'unknown')}`",
        f"- Repository Test Evidence: `{summary.get('repository_test_evidence_status', 'not_started')}`",
        "",
        "## Issues",
        "",
        "| Severity | Stage | Code | Count | Message |",
        "| --- | --- | --- | ---: | --- |",
    ]
    if issues:
        for issue_value in issues:
            issue = _dict(issue_value)
            lines.append(
                "| "
                f"{_markdown_cell(issue.get('severity', ''))} | "
                f"{_markdown_cell(issue.get('stage', ''))} | "
                f"{_markdown_cell(issue.get('code', ''))} | "
                f"{_int(issue.get('count', 0))} | "
                f"{_markdown_cell(issue.get('message', ''))} |"
            )
    else:
        lines.append("| none |  |  | 0 | No diagnostic issues detected. |")

    for issue_value in issues:
        issue = _dict(issue_value)
        examples = _list(issue.get("examples"))
        next_steps = _list(issue.get("next_steps"))
        lines.extend(
            [
                "",
                f"## {_markdown_cell(issue.get('code', 'issue'))}",
                "",
                f"- Stage: `{_markdown_cell(issue.get('stage', ''))}`",
                f"- Severity: `{_markdown_cell(issue.get('severity', ''))}`",
                f"- Message: {_markdown_cell(issue.get('message', ''))}",
            ]
        )
        if examples:
            lines.extend(
                [
                    "",
                    "| Example | Details |",
                    "| --- | --- |",
                ]
            )
            for example in examples[:10]:
                item = _dict(example)
                name = (
                    item.get("target_path")
                    or item.get("source_path")
                    or item.get("check")
                    or item.get("recipe")
                    or "example"
                )
                details = {
                    key: value
                    for key, value in item.items()
                    if key not in {"target_path", "source_path", "check", "recipe"}
                }
                lines.append(
                    "| "
                    f"{_markdown_cell(name)} | "
                    f"{_markdown_cell(json.dumps(details, ensure_ascii=False, default=str))} |"
                )
        if next_steps:
            lines.extend(["", "Next steps:"])
            for step in next_steps:
                lines.append(f"- {_markdown_cell(step)}")

    next_actions = _list(diagnostics.get("next_actions"))
    if next_actions:
        lines.extend(["", "## Next Actions", ""])
        for action in next_actions:
            lines.append(f"- {_markdown_cell(action)}")
    benchmarkization = _dict(diagnostics.get("benchmarkization_readiness"))
    if benchmarkization:
        lines.extend(
            [
                "",
                "## Benchmarkization Readiness",
                "",
                f"- Status: `{benchmarkization.get('status', 'unknown')}`",
                f"- Stage: `{benchmarkization.get('stage', 'unknown')}`",
                f"- Ready: {str(bool(benchmarkization.get('ready', False))).lower()}",
                f"- Benchmark Run Present: {str(bool(benchmarkization.get('benchmark_run_present', False))).lower()}",
                f"- Benchmark Cases: {_int(benchmarkization.get('benchmark_cases', 0))}",
                (
                    "- Repository Test Evidence: "
                    f"`{_markdown_cell(benchmarkization.get('repository_test_evidence_status') or 'not_started')}`"
                ),
                (
                    "- Blocking Reasons: "
                    f"`{_markdown_cell(', '.join(str(item) for item in _list(benchmarkization.get('blocking_reasons'))) or 'none')}`"
                ),
                "",
                "| Check | Stage | Passed | Expected | Actual |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for check_value in _list(benchmarkization.get("checks")):
            check = _dict(check_value)
            lines.append(
                "| "
                f"{_markdown_cell(check.get('name', ''))} | "
                f"{_markdown_cell(check.get('stage', ''))} | "
                f"{str(bool(check.get('passed', False))).lower()} | "
                f"{_markdown_cell(check.get('expected', ''))} | "
                f"{_markdown_cell(check.get('actual', ''))} |"
            )
        remediation_plan = _dict(benchmarkization.get("remediation_plan"))
        remediation_actions = _list(remediation_plan.get("actions"))
        if remediation_actions:
            lines.extend(
                [
                    "",
                    "### Remediation Plan",
                    "",
                    "| Action | Stage | Auto | Risk | Command |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for action_value in remediation_actions:
                action = _dict(action_value)
                lines.append(
                    "| "
                    f"{_markdown_cell(action.get('action_id', ''))} | "
                    f"{_markdown_cell(action.get('stage', ''))} | "
                    f"{str(bool(action.get('auto_runnable', False))).lower()} | "
                    f"{_markdown_cell(action.get('risk', ''))} | "
                    f"`{_markdown_cell(action.get('command') or 'none')}` |"
                )
    recipe_suggestions = _list(diagnostics.get("recipe_suggestions"))
    if recipe_suggestions:
        lines.extend(
            [
                "",
                "## Recipe Suggestions",
                "",
                "| Recipe | Misses | Top Reasons | Suggested Actions |",
                "| --- | ---: | --- | --- |",
            ]
        )
        for suggestion_value in recipe_suggestions:
            suggestion = _dict(suggestion_value)
            reasons = ", ".join(
                str(reason.get("reason", ""))
                for reason in _list(suggestion.get("top_reasons"))
            )
            actions = "; ".join(str(action) for action in _list(suggestion.get("suggested_actions")))
            lines.append(
                "| "
                f"{_markdown_cell(suggestion.get('recipe', ''))} | "
                f"{_int(suggestion.get('miss_count', 0))} | "
                f"{_markdown_cell(reasons)} | "
                f"{_markdown_cell(actions)} |"
            )
    repository_test_readiness = _dict(
        diagnostics.get("repository_test_readiness")
    )
    if repository_test_readiness:
        lines.extend(
            [
                "",
                "## Repository Test Evidence Readiness",
                "",
                f"- Status: `{repository_test_readiness.get('status', 'not_started')}`",
                "- Runtime Evidence Chain: "
                f"{repository_test_readiness.get('runtime_evidence_chain_present', False)}",
                "- Benchmark Evidence Chain: "
                f"{repository_test_readiness.get('benchmark_evidence_chain_present', False)}",
                "- Public API Trace: "
                f"{repository_test_readiness.get('public_api_trace_present', False)}",
                "- Overlay Case Context: "
                f"{repository_test_readiness.get('overlay_case_context_present', False)}",
            ]
        )
        trigger_expression = str(
            repository_test_readiness.get("trigger_expression") or ""
        )
        public_entrypoint = str(
            repository_test_readiness.get("public_entrypoint") or ""
        )
        internal_target = str(
            repository_test_readiness.get("internal_target") or ""
        )
        if trigger_expression or public_entrypoint or internal_target:
            lines.append(
                "- Public API Route: "
                f"`{_markdown_cell(trigger_expression)}` -> "
                f"`{_markdown_cell(public_entrypoint)}` -> "
                f"`{_markdown_cell(internal_target)}`"
            )
    return "\n".join(lines)


def evaluate_onboarding_quality_gate(
    report: GitHubBenchmarkOnboardingReport,
    thresholds: OnboardingQualityGateThresholds | None = None,
) -> OnboardingQualityGateResult:
    thresholds = thresholds or OnboardingQualityGateThresholds()
    quality = _dict(report.quality_summary)
    selection_audit = build_onboarding_selection_audit(report)
    source_diversity = _dict(selection_audit.get("source_diversity"))
    candidate_diversity = _dict(selection_audit.get("candidate_diversity"))
    checks = [
        _int_check(
            "selected_sources",
            report.selected_source_count,
            thresholds.min_imported_sources,
        ),
        _int_check(
            "generated_candidates",
            report.generated_candidate_count,
            thresholds.min_generated_candidates,
        ),
        _float_check(
            "quality_score",
            _float(quality.get("quality_score", 0.0)),
            thresholds.min_quality_score,
        ),
        _float_check(
            "source_hit_rate",
            _float(quality.get("source_hit_rate", 0.0)),
            thresholds.min_source_hit_rate,
        ),
        _int_check(
            "selected_source_groups",
            _int(quality.get("selected_source_group_count", 0)),
            thresholds.min_selected_source_groups,
        ),
        _int_check(
            "selected_source_directories",
            _int(quality.get("selected_source_directory_count", 0)),
            thresholds.min_selected_source_directories,
        ),
        _int_check(
            "selected_rules",
            _int(quality.get("selected_rule_count", 0)),
            thresholds.min_selected_rules,
        ),
        _int_check(
            "selected_bug_types",
            _int(quality.get("selected_bug_type_count", 0)),
            thresholds.min_selected_bug_types,
        ),
        _float_check(
            "source_group_coverage",
            _float(source_diversity.get("source_group_coverage", 0.0)),
            thresholds.min_source_group_coverage,
        ),
        _float_check(
            "source_directory_coverage",
            _float(source_diversity.get("source_directory_coverage", 0.0)),
            thresholds.min_source_directory_coverage,
        ),
        _float_check(
            "candidate_rule_coverage",
            _float(candidate_diversity.get("rule_coverage", 0.0)),
            thresholds.min_candidate_rule_coverage,
        ),
        _float_check(
            "candidate_bug_type_coverage",
            _float(candidate_diversity.get("bug_type_coverage", 0.0)),
            thresholds.min_candidate_bug_type_coverage,
        ),
        _float_check(
            "candidate_source_coverage",
            _float(candidate_diversity.get("candidate_source_coverage", 0.0)),
            thresholds.min_candidate_source_coverage,
        ),
    ]
    if thresholds.require_ready_for_benchmark:
        checks.append(
            OnboardingQualityGateCheck(
                name="ready_for_benchmark",
                passed=report.ready_for_benchmark,
                expected="true",
                actual=str(report.ready_for_benchmark).lower(),
            )
        )
    benchmark_present = report.benchmark_run is not None
    if thresholds.require_benchmark_run:
        checks.append(
            OnboardingQualityGateCheck(
                name="benchmark_run_present",
                passed=benchmark_present,
                expected="true",
                actual=str(benchmark_present).lower(),
            )
        )
    if benchmark_present:
        benchmark = _dict(report.benchmark_run)
        summary = _dict(benchmark.get("summary"))
        checks.extend(
            [
                OnboardingQualityGateCheck(
                    name="benchmark_template_validation",
                    passed=bool(
                        _dict(benchmark.get("template_validation")).get("is_valid")
                    ),
                    expected="valid",
                    actual=str(
                        _dict(benchmark.get("template_validation")).get("is_valid")
                    ).lower(),
                ),
                OnboardingQualityGateCheck(
                    name="benchmark_manifest_validation",
                    passed=bool(
                        _dict(benchmark.get("manifest_validation")).get("is_valid")
                    ),
                    expected="valid",
                    actual=str(
                        _dict(benchmark.get("manifest_validation")).get("is_valid")
                    ).lower(),
                ),
                _int_check(
                    "benchmark_cases",
                    _int(summary.get("case_count", 0)),
                    thresholds.min_benchmark_cases,
                ),
                _float_check(
                    "benchmark_top1",
                    _float(summary.get("top1", 0.0)),
                    thresholds.min_top1,
                ),
                _float_check(
                    "benchmark_map",
                    _float(summary.get("map", 0.0)),
                    thresholds.min_map,
                ),
                _float_check(
                    "benchmark_patch_success_rate",
                    _float(summary.get("patch_success_rate", 0.0)),
                    thresholds.min_patch_success_rate,
                ),
            ]
        )
    return OnboardingQualityGateResult(
        passed=all(check.passed for check in checks),
        thresholds=thresholds,
        checks=checks,
    )


def render_onboarding_quality_gate_markdown(
    result: OnboardingQualityGateResult,
) -> str:
    lines = [
        "# Onboarding Quality Gate",
        "",
        f"- Status: {'PASS' if result.passed else 'FAIL'}",
        "",
        "| Check | Status | Expected | Actual | Details |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in result.checks:
        lines.append(
            "| "
            f"{_markdown_cell(check.name)} | "
            f"{'PASS' if check.passed else 'FAIL'} | "
            f"{_markdown_cell(check.expected)} | "
            f"{_markdown_cell(check.actual)} | "
            f"{_markdown_cell('; '.join(check.details or []))} |"
        )
    return "\n".join(lines)


def build_onboarding_selection_audit(
    report: GitHubBenchmarkOnboardingReport,
) -> dict[str, Any]:
    all_sources = [
        source
        for source in report.import_report.source_entries
        if isinstance(source, dict)
    ]
    selected_sources = _selected_source_entries(report)
    selected_source_keys = {_source_identity(source) for source in selected_sources}
    omitted_sources = [
        source
        for source in all_sources
        if _source_identity(source) not in selected_source_keys
    ]
    all_source_groups = {_source_group_key(source) for source in all_sources}
    selected_source_groups = {
        _source_group_key(source) for source in selected_sources
    }
    all_source_directories = {_source_directory_key(source) for source in all_sources}
    selected_source_directories = {
        _source_directory_key(source) for source in selected_sources
    }

    all_candidates = [
        candidate
        for candidate in report.mining_report.candidates
        if isinstance(candidate, dict)
    ]
    selected_candidates = _selected_candidates(report)
    selected_candidate_keys = {
        _candidate_identity(candidate) for candidate in selected_candidates
    }
    omitted_candidates = [
        candidate
        for candidate in all_candidates
        if _candidate_identity(candidate) not in selected_candidate_keys
    ]
    all_rule_counts = _candidate_rule_counts(all_candidates)
    selected_rule_counts = _candidate_rule_counts(selected_candidates)
    omitted_rule_counts = _candidate_rule_counts(omitted_candidates)
    all_bug_type_counts = _candidate_bug_type_counts(all_candidates)
    selected_bug_type_counts = _candidate_bug_type_counts(selected_candidates)
    omitted_bug_type_counts = _candidate_bug_type_counts(omitted_candidates)
    all_candidate_sources = set().union(
        *(_candidate_source_keys(candidate) for candidate in all_candidates)
    ) if all_candidates else set()
    selected_candidate_sources = set().union(
        *(_candidate_source_keys(candidate) for candidate in selected_candidates)
    ) if selected_candidates else set()
    source_cache_dir = report.output_paths.get("source_cache_dir")
    selected_recipes = [
        str(recipe) for recipe in _list(report.quality_summary.get("selected_recipes"))
    ]
    return {
        "headline": {
            "mode": report.mode,
            "preset": report.preset,
            "source": report.source,
            "imported_sources": report.imported_source_count,
            "selected_sources": report.selected_source_count,
            "source_limit": report.source_limit,
            "source_limit_applied": bool(
                report.quality_summary.get("source_limit_applied", False)
            ),
            "source_limit_strategy": report.quality_summary.get(
                "source_limit_strategy", "all"
            ),
            "generated_candidates": report.generated_candidate_count,
            "candidate_limit": report.candidate_limit,
            "candidate_limit_applied": bool(
                report.quality_summary.get("candidate_limit_applied", False)
            ),
            "candidate_limit_strategy": report.quality_summary.get(
                "candidate_limit_strategy", "all"
            ),
        },
        "source_diversity": {
            "imported_source_count": len(all_sources),
            "selected_source_count": len(selected_sources),
            "omitted_source_count": len(omitted_sources),
            "all_source_group_count": len(all_source_groups),
            "selected_source_group_count": _int(
                report.quality_summary.get("selected_source_group_count", 0)
            ),
            "source_group_coverage": _ratio(
                len(selected_source_groups),
                len(all_source_groups),
            ),
            "all_source_directory_count": len(all_source_directories),
            "selected_source_directory_count": _int(
                report.quality_summary.get("selected_source_directory_count", 0)
            ),
            "source_directory_coverage": _ratio(
                len(selected_source_directories),
                len(all_source_directories),
            ),
            "all_source_groups": sorted(all_source_groups),
            "selected_source_groups": sorted(selected_source_groups),
            "all_source_directories": sorted(all_source_directories),
            "selected_source_directories": sorted(selected_source_directories),
            "omitted_source_group_counts": _count_source_groups(omitted_sources),
            "omitted_source_directory_counts": _count_source_directories(
                omitted_sources
            ),
        },
        "candidate_diversity": {
            "unlimited_candidate_count": len(all_candidates),
            "selected_candidate_count": len(selected_candidates),
            "omitted_candidate_count": len(omitted_candidates),
            "all_rule_count": len(all_rule_counts),
            "selected_rule_count": _int(
                report.quality_summary.get("selected_rule_count", 0)
            ),
            "rule_coverage": _ratio(len(selected_rule_counts), len(all_rule_counts)),
            "all_bug_type_count": len(all_bug_type_counts),
            "selected_bug_type_count": _int(
                report.quality_summary.get("selected_bug_type_count", 0)
            ),
            "bug_type_coverage": _ratio(
                len(selected_bug_type_counts),
                len(all_bug_type_counts),
            ),
            "all_candidate_source_count": len(all_candidate_sources),
            "selected_candidate_source_count": _int(
                report.quality_summary.get("selected_candidate_source_count", 0)
            ),
            "candidate_source_coverage": _ratio(
                len(selected_candidate_sources),
                len(all_candidate_sources),
            ),
            "all_rule_counts": all_rule_counts,
            "rule_counts": selected_rule_counts,
            "omitted_rule_counts": omitted_rule_counts,
            "all_bug_type_counts": all_bug_type_counts,
            "bug_type_counts": selected_bug_type_counts,
            "omitted_bug_type_counts": omitted_bug_type_counts,
            "omitted_candidate_ids_preview": [
                _candidate_display_id(candidate)
                for candidate in omitted_candidates[:20]
            ],
        },
        "selected_sources": [
            _source_selection_audit_row(
                source,
                recipes=selected_recipes,
                source_cache_dir=source_cache_dir,
            )
            for source in selected_sources
        ],
        "omitted_sources_preview": [
            _source_selection_audit_row(
                source,
                recipes=selected_recipes,
                source_cache_dir=source_cache_dir,
            )
            for source in omitted_sources[:20]
        ],
        "selected_candidates": [
            _candidate_showcase_row(candidate) for candidate in selected_candidates
        ],
    }


def render_onboarding_selection_audit_markdown(
    audit: dict[str, Any],
) -> str:
    headline = _dict(audit.get("headline"))
    source_diversity = _dict(audit.get("source_diversity"))
    candidate_diversity = _dict(audit.get("candidate_diversity"))
    lines = [
        "# GitHub Onboarding Selection Audit",
        "",
        "## Summary",
        "",
        f"- Preset: `{headline.get('preset', 'manual')}`",
        f"- Source: `{headline.get('source', '')}`",
        f"- Imported Sources: {_int(headline.get('imported_sources', 0))}",
        f"- Selected Sources: {_int(headline.get('selected_sources', 0))}",
        f"- Source Limit: {headline.get('source_limit') or 'none'}",
        f"- Source Strategy: {headline.get('source_limit_strategy', 'all')}",
        f"- Generated Candidates: {_int(headline.get('generated_candidates', 0))}",
        f"- Candidate Limit: {headline.get('candidate_limit') or 'none'}",
        (
            "- Candidate Strategy: "
            f"{headline.get('candidate_limit_strategy', 'all')}"
        ),
        (
            "- Source Diversity: "
            f"groups={_int(source_diversity.get('selected_source_group_count', 0))}"
            f"/{_int(source_diversity.get('all_source_group_count', 0))} "
            f"({_format_ratio(source_diversity.get('source_group_coverage'))}), "
            f"directories={_int(source_diversity.get('selected_source_directory_count', 0))}"
            f"/{_int(source_diversity.get('all_source_directory_count', 0))} "
            f"({_format_ratio(source_diversity.get('source_directory_coverage'))})"
        ),
        (
            "- Candidate Diversity: "
            f"rules={_int(candidate_diversity.get('selected_rule_count', 0))}"
            f"/{_int(candidate_diversity.get('all_rule_count', 0))} "
            f"({_format_ratio(candidate_diversity.get('rule_coverage'))}), "
            f"bug_types={_int(candidate_diversity.get('selected_bug_type_count', 0))}"
            f"/{_int(candidate_diversity.get('all_bug_type_count', 0))} "
            f"({_format_ratio(candidate_diversity.get('bug_type_coverage'))}), "
            f"sources={_int(candidate_diversity.get('selected_candidate_source_count', 0))}"
            f"/{_int(candidate_diversity.get('all_candidate_source_count', 0))} "
            f"({_format_ratio(candidate_diversity.get('candidate_source_coverage'))})"
        ),
        "",
        "## Omitted Summary",
        "",
        "| Area | Omitted | Details |",
        "| --- | ---: | --- |",
        (
            "| Sources | "
            f"{_int(source_diversity.get('omitted_source_count', 0))} | "
            f"groups={_markdown_cell(_format_counts(_dict(source_diversity.get('omitted_source_group_counts'))))}; "
            f"directories={_markdown_cell(_format_counts(_dict(source_diversity.get('omitted_source_directory_counts'))))} |"
        ),
        (
            "| Candidates | "
            f"{_int(candidate_diversity.get('omitted_candidate_count', 0))} | "
            f"rules={_markdown_cell(_format_counts(_dict(candidate_diversity.get('omitted_rule_counts'))))}; "
            f"bug_types={_markdown_cell(_format_counts(_dict(candidate_diversity.get('omitted_bug_type_counts'))))}; "
            f"ids={_markdown_cell(_format_list(_list(candidate_diversity.get('omitted_candidate_ids_preview'))))} |"
        ),
        "",
        "## Selected Sources",
        "",
        "| Target | Directory | Preferred | Layout | Recipe | Total | Upstream | Ref | Source Path | SHA256 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for source in _list(audit.get("selected_sources")):
        source_row = _dict(source)
        lines.append(
            "| "
            f"{_markdown_cell(source_row.get('target_path', ''))} | "
            f"{_markdown_cell(source_row.get('directory', ''))} | "
            f"{str(bool(source_row.get('preferred_mining_source'))).lower()} | "
            f"{_int(source_row.get('layout_score', 0))} | "
            f"{_int(source_row.get('recipe_score', 0))} | "
            f"{_int(source_row.get('total_score', 0))} | "
            f"{_markdown_cell(source_row.get('upstream', ''))} | "
            f"{_markdown_cell(source_row.get('ref', ''))} | "
            f"{_markdown_cell(source_row.get('source_path', ''))} | "
            f"{'yes' if source_row.get('sha256_present') else 'no'} |"
        )
    omitted_preview = _list(audit.get("omitted_sources_preview"))
    if omitted_preview:
        lines.extend(
            [
                "",
                "## Omitted Sources Preview",
                "",
                "| Target | Directory | Preferred | Layout | Recipe | Total | Source Path |",
                "| --- | --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for source in omitted_preview:
            source_row = _dict(source)
            lines.append(
                "| "
                f"{_markdown_cell(source_row.get('target_path', ''))} | "
                f"{_markdown_cell(source_row.get('directory', ''))} | "
                f"{str(bool(source_row.get('preferred_mining_source'))).lower()} | "
                f"{_int(source_row.get('layout_score', 0))} | "
                f"{_int(source_row.get('recipe_score', 0))} | "
                f"{_int(source_row.get('total_score', 0))} | "
                f"{_markdown_cell(source_row.get('source_path', ''))} |"
            )
    lines.extend(
        [
            "",
            "## Selected Candidates",
            "",
            "| Candidate | Rules | Bug Type | Function | Target | Case |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for candidate in _list(audit.get("selected_candidates")):
        candidate_row = _dict(candidate)
        lines.append(
            "| "
            f"{_markdown_cell(candidate_row.get('id', ''))} | "
            f"{_markdown_cell(', '.join(map(str, _list(candidate_row.get('rule_ids')))))} | "
            f"{_markdown_cell(candidate_row.get('bug_type', ''))} | "
            f"{_markdown_cell(candidate_row.get('function_name', ''))} | "
            f"{_markdown_cell(candidate_row.get('target_path', ''))} | "
            f"{_markdown_cell(candidate_row.get('case_name', ''))} |"
        )
    return "\n".join(lines)


def build_onboarding_showcase_lite(
    report: GitHubBenchmarkOnboardingReport,
) -> dict[str, Any]:
    benchmark_summary = (
        _dict(report.benchmark_run.get("summary"))
        if report.benchmark_run is not None
        else {}
    )
    quality_gate = _dict(report.quality_gate)
    mining_payload = report.mining_report.to_dict()
    return {
        "headline": {
            "mode": report.mode,
            "preset": report.preset,
            "source": report.source,
            "imported_sources": report.imported_source_count,
            "selected_sources": report.selected_source_count,
            "generated_candidates": report.generated_candidate_count,
            "source_limit": report.source_limit,
            "source_limit_strategy": report.quality_summary.get(
                "source_limit_strategy"
            ),
            "candidate_limit": report.candidate_limit,
            "candidate_limit_strategy": report.quality_summary.get(
                "candidate_limit_strategy"
            ),
            "source_group_coverage": _float(
                report.quality_summary.get("source_group_coverage", 0.0)
            ),
            "source_directory_coverage": _float(
                report.quality_summary.get("source_directory_coverage", 0.0)
            ),
            "candidate_rule_coverage": _float(
                report.quality_summary.get("candidate_rule_coverage", 0.0)
            ),
            "candidate_bug_type_coverage": _float(
                report.quality_summary.get("candidate_bug_type_coverage", 0.0)
            ),
            "candidate_source_coverage": _float(
                report.quality_summary.get("candidate_source_coverage", 0.0)
            ),
            "ready_for_benchmark": report.ready_for_benchmark,
            "quality_score": _float(report.quality_summary.get("quality_score", 0.0)),
            "quality_gate_passed": quality_gate.get("passed")
            if quality_gate
            else None,
            "benchmark_cases": _int(benchmark_summary.get("case_count", 0)),
            "benchmark_top1": _float(benchmark_summary.get("top1", 0.0)),
            "benchmark_map": _float(benchmark_summary.get("map", 0.0)),
            "benchmark_patch_success_rate": _float(
                benchmark_summary.get("patch_success_rate", 0.0)
            ),
        },
        "rules": dict(report.mining_report.rule_counts),
        "bug_types": dict(report.mining_report.bug_type_counts),
        "sources": [
            _source_showcase_row(source)
            for source in _selected_source_entries(report)
        ],
        "candidate_preview": [
            _candidate_showcase_row(candidate)
            for candidate in _selected_candidates(report)[:10]
        ],
        "benchmark_cases": (
            list(report.benchmark_run.get("cases", []))
            if report.benchmark_run is not None
            else []
        ),
        "quality_gate_failed_checks": [
            check
            for check in _list(quality_gate.get("checks"))
            if not _dict(check).get("passed", False)
        ],
        "artifacts": dict(report.output_paths),
        "mining_quality_summary": dict(report.quality_summary),
        "source_candidate_count": len(_list(mining_payload.get("source_candidates"))),
    }


def render_onboarding_showcase_lite_markdown(showcase: dict[str, Any]) -> str:
    headline = _dict(showcase.get("headline"))
    lines = [
        "# GitHub Onboarding Showcase Lite",
        "",
        "## Summary",
        "",
        f"- Source: `{headline.get('source', '')}`",
        f"- Mode: `{headline.get('mode', '')}`",
        f"- Preset: `{headline.get('preset', 'manual')}`",
        f"- Imported Sources: {_int(headline.get('imported_sources', 0))}",
        f"- Selected Sources: {_int(headline.get('selected_sources', 0))}",
        f"- Generated Candidates: {_int(headline.get('generated_candidates', 0))}",
        (
            "- Source Coverage: "
            f"groups={_format_ratio(headline.get('source_group_coverage'))}, "
            f"directories={_format_ratio(headline.get('source_directory_coverage'))}"
        ),
        (
            "- Candidate Coverage: "
            f"rules={_format_ratio(headline.get('candidate_rule_coverage'))}, "
            f"bug_types={_format_ratio(headline.get('candidate_bug_type_coverage'))}, "
            f"sources={_format_ratio(headline.get('candidate_source_coverage'))}"
        ),
        f"- Ready For Benchmark: {headline.get('ready_for_benchmark', False)}",
        f"- Quality Score: {_float(headline.get('quality_score', 0.0)):.3f}",
    ]
    if headline.get("quality_gate_passed") is not None:
        lines.append(
            f"- Quality Gate: {'PASS' if headline.get('quality_gate_passed') else 'FAIL'}"
        )
    if _int(headline.get("benchmark_cases", 0)) > 0:
        lines.extend(
            [
                f"- Benchmark Cases: {_int(headline.get('benchmark_cases', 0))}",
                f"- Top-1: {_float(headline.get('benchmark_top1', 0.0)):.4f}",
                f"- MAP: {_float(headline.get('benchmark_map', 0.0)):.4f}",
                (
                    "- Patch Success: "
                    f"{_float(headline.get('benchmark_patch_success_rate', 0.0)):.4f}"
                ),
            ]
        )
    lines.extend(
        [
            "",
            "## Rule Coverage",
            "",
            "| Rule | Candidates |",
            "| --- | ---: |",
        ]
    )
    for rule, count in sorted(_dict(showcase.get("rules")).items()):
        lines.append(f"| {_markdown_cell(rule)} | {_int(count)} |")
    if not _dict(showcase.get("rules")):
        lines.append("| none | 0 |")

    lines.extend(
        [
            "",
            "## Source Preview",
            "",
            "| Target | Upstream | Ref | Source Path |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in _list(showcase.get("sources"))[:10]:
        item = _dict(row)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('target_path', ''))} | "
            f"{_markdown_cell(item.get('upstream', ''))} | "
            f"{_markdown_cell(item.get('ref', ''))} | "
            f"{_markdown_cell(item.get('source_path', ''))} |"
        )

    lines.extend(
        [
            "",
            "## Candidate Preview",
            "",
            "| Candidate | Rule IDs | Bug Type | Function | Target |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in _list(showcase.get("candidate_preview")):
        item = _dict(row)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('id', ''))} | "
            f"{_markdown_cell(', '.join(_list(item.get('rule_ids'))))} | "
            f"{_markdown_cell(item.get('bug_type', ''))} | "
            f"{_markdown_cell(item.get('function_name', ''))} | "
            f"{_markdown_cell(item.get('target_path', ''))} |"
        )
    if not _list(showcase.get("candidate_preview")):
        lines.append("| none |  |  |  |  |")

    if _list(showcase.get("benchmark_cases")):
        lines.extend(
            [
                "",
                "## Benchmark Result Preview",
                "",
                "| Case | Top Function | Best Rule | Patch Success |",
                "| --- | --- | --- | --- |",
            ]
        )
        for row in _list(showcase.get("benchmark_cases"))[:10]:
            item = _dict(row)
            lines.append(
                "| "
                f"{_markdown_cell(item.get('name', ''))} | "
                f"{_markdown_cell(item.get('top_function', ''))} | "
                f"{_markdown_cell(item.get('best_patch_rule_id', ''))} | "
                f"{_markdown_cell(item.get('patch_success', ''))} |"
            )

    failed = _list(showcase.get("quality_gate_failed_checks"))
    if failed:
        lines.extend(
            [
                "",
                "## Gate Failures",
                "",
                "| Check | Expected | Actual |",
                "| --- | --- | --- |",
            ]
        )
        for check in failed:
            item = _dict(check)
            lines.append(
                "| "
                f"{_markdown_cell(item.get('name', ''))} | "
                f"{_markdown_cell(item.get('expected', ''))} | "
                f"{_markdown_cell(item.get('actual', ''))} |"
            )

    lines.extend(
        [
            "",
            "## Key Artifacts",
            "",
            "| Artifact | Path |",
            "| --- | --- |",
        ]
    )
    for name, path in _dict(showcase.get("artifacts")).items():
        lines.append(f"| {_markdown_cell(name)} | `{_markdown_cell(path)}` |")
    return "\n".join(lines)


def build_onboarding_recipe_selection(
    sources_payload: dict[str, Any],
    *,
    requested_recipes: list[str] | None,
    source_cache_dir: str | Path | None,
    max_auto_recipes: int = DEFAULT_AUTO_RECIPE_LIMIT,
) -> dict[str, Any]:
    sources = [source for source in _list(sources_payload.get("sources")) if _dict(source)]
    recipe_scores = {recipe: 0 for recipe in sorted(SUPPORTED_RECIPES)}
    recipe_max_scores = {recipe: 0 for recipe in sorted(SUPPORTED_RECIPES)}
    source_rows: list[dict[str, Any]] = []
    for source in sources:
        source_dict = _dict(source)
        path_text = (
            str(source_dict.get("source_path", ""))
            + "\n"
            + str(source_dict.get("target_path", ""))
        ).lower()
        source_text = _read_source_text_for_selection(source_dict, source_cache_dir)
        per_recipe: dict[str, int] = {}
        for recipe in sorted(SUPPORTED_RECIPES):
            score = _single_recipe_score(recipe, path_text, source_text.lower())
            per_recipe[recipe] = score
            recipe_scores[recipe] += score
            recipe_max_scores[recipe] = max(recipe_max_scores[recipe], score)
        source_rows.append(
            {
                "target_path": str(source_dict.get("target_path", "")),
                "source_path": str(source_dict.get("source_path", "")),
                "top_recipes": [
                    {"recipe": recipe, "score": score}
                    for recipe, score in sorted(
                        per_recipe.items(), key=lambda item: (-item[1], item[0])
                    )[:5]
                    if score > 0
                ],
            }
        )
    ranked = [
        {
            "recipe": recipe,
            "score": recipe_scores[recipe],
            "max_source_score": recipe_max_scores[recipe],
        }
        for recipe in sorted(
            recipe_scores,
            key=lambda item: (
                -recipe_max_scores[item],
                -recipe_scores[item],
                item,
            ),
        )
        if recipe_scores[recipe] > 0
    ]
    if requested_recipes:
        selected = [str(recipe) for recipe in requested_recipes]
        mode = "explicit"
    elif ranked:
        selected = [str(item["recipe"]) for item in ranked[:max_auto_recipes]]
        mode = "auto_topk"
    else:
        selected = sorted(SUPPORTED_RECIPES)
        mode = "auto_fallback_all"
    return {
        "mode": mode,
        "source_count": len(sources),
        "max_auto_recipes": max_auto_recipes,
        "requested_recipes": [str(recipe) for recipe in requested_recipes or []],
        "selected_recipes": selected,
        "recommended_count": len(ranked),
        "recommended_recipes": ranked,
        "source_rows": source_rows,
    }


def render_onboarding_recipe_selection_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# GitHub Onboarding Recipe Selection",
        "",
        f"- Mode: `{payload.get('mode', '')}`",
        f"- Sources: {_int(payload.get('source_count', 0))}",
        f"- Max Auto Recipes: {_int(payload.get('max_auto_recipes', 0))}",
        (
            "- Selected Recipes: "
            f"{', '.join(str(item) for item in _list(payload.get('selected_recipes')))}"
        ),
        f"- Recommended Recipes: {_int(payload.get('recommended_count', 0))}",
        "",
        "## Recipe Scores",
        "",
        "| Recipe | Max Source Score | Total Score |",
        "| --- | ---: | ---: |",
    ]
    for item in _list(payload.get("recommended_recipes")):
        row = _dict(item)
        lines.append(
            f"| {_markdown_cell(row.get('recipe', ''))} | "
            f"{_int(row.get('max_source_score', 0))} | "
            f"{_int(row.get('score', 0))} |"
        )
    lines.extend(
        [
            "",
            "## Source Top Recipes",
            "",
            "| Source | Top Recipes |",
            "| --- | --- |",
        ]
    )
    for item in _list(payload.get("source_rows")):
        row = _dict(item)
        top = ", ".join(
            f"{_dict(recipe).get('recipe', '')}:{_int(_dict(recipe).get('score', 0))}"
            for recipe in _list(row.get("top_recipes"))
        )
        lines.append(
            "| "
            f"{_markdown_cell(row.get('source_path') or row.get('target_path', ''))} | "
            f"{_markdown_cell(top)} |"
        )
    return "\n".join(lines)


def build_onboarding_run_config(
    report: GitHubBenchmarkOnboardingReport,
    *,
    materialize_template: bool,
    run_benchmark: bool,
    benchmark_output_dir: str | Path | None,
    patch_mode: str,
    judge_mode: str,
    patch_judge_mode: str,
    llm_score_mode: str,
    use_dynamic_coverage: bool,
    run_quality_gate: bool,
    quality_gate_thresholds: OnboardingQualityGateThresholds,
    auto_dependency_sources: bool,
    dependency_max_depth: int,
    recipe_selection: dict[str, Any],
    run_showcase_lite: bool,
    run_smoke_validation: bool,
    run_repository_test_command: bool,
    run_repository_test_environment_setup: bool,
    run_repository_test_retry: bool,
    run_repository_test_retry_prerequisites: bool,
    auto_repository_test_retry: bool,
    auto_repository_test_retry_max_risk: str,
    auto_repository_test_retry_allowed_runners: list[str] | None,
    repository_test_root: str | Path | None,
    repository_test_timeout: int,
    repository_test_failure_overlay_candidate_limit: int,
    repository_test_patch_validation_limit: int,
    repository_patch_generation_mode: str = "rule",
    repository_llm_patch_candidate_limit: int | None = None,
    repository_patch_candidate_variant_allowlist: list[str] | None = None,
    repository_test_reflection_mode: str,
    repository_test_reflection_rounds: int,
    repository_test_reflection_width: int,
    repository_test_environment_setup_timeout: int,
    checkout_repository_tests: bool,
    repository_checkout_timeout: int,
    repository_checkout_depth: int,
) -> dict[str, Any]:
    repository_test = _dict(report.repository_test_command)
    repository_environment = _dict(report.repository_test_environment)
    repository_environment_setup = _dict(report.repository_test_environment_setup)
    repository_environment_setup_result = _dict(
        report.repository_test_environment_setup_result
    )
    repository_execution_plan = _dict(report.repository_test_execution_plan)
    repository_execution_result = _dict(report.repository_test_execution_result)
    repository_retry_plan = _dict(report.repository_test_retry_plan)
    repository_retry_execution_result = _dict(
        report.repository_test_retry_execution_result
    )
    repository_setup_doctor = _dict(report.repository_test_setup_doctor)
    repository_pytest_plugin_repair = _dict(report.repository_test_pytest_plugin_repair)
    repository_pytest_plugin_repair_retry_execution_result = _dict(
        report.repository_test_pytest_plugin_repair_retry_execution_result
    )
    repository_timeout_narrowing = _dict(report.repository_test_timeout_narrowing)
    repository_dynamic_evidence = _dict(report.repository_test_dynamic_evidence)
    repository_effective_execution_result = (
        _repository_test_effective_execution_result_summary(
            repository_execution_result=repository_execution_result,
            repository_retry_execution_result=repository_retry_execution_result,
            repository_pytest_plugin_repair_retry_execution_result=(
                repository_pytest_plugin_repair_retry_execution_result
            ),
            repository_timeout_narrowing=repository_timeout_narrowing,
            repository_dynamic_evidence=repository_dynamic_evidence,
        )
    )
    repository_failure_overlay = _dict(report.repository_test_failure_overlay)
    repository_analysis_route = _repository_test_analysis_route(
        natural_evidence=repository_dynamic_evidence,
        failure_overlay=repository_failure_overlay,
        execution_plan=repository_execution_plan,
    )
    repository_fault_localization = _dict(report.repository_test_fault_localization)
    repository_patch_candidates = _dict(report.repository_test_patch_candidates)
    repository_patch_validation = _dict(report.repository_test_patch_validation)
    repository_repair_summary = _dict(report.repository_test_repair_summary)
    repository_best_patch = _dict(repository_patch_validation.get("best_patch"))
    repository_regression_validation = _dict(
        repository_patch_validation.get("regression_validation")
    )
    repository_checkout = _dict(report.repository_checkout)
    repository_checkout_sources = _dict(report.repository_checkout_sources)
    repository_config_snapshot = _dict(report.repository_config_snapshot)
    checkout_source_metadata = _dict(repository_checkout_sources.get("discovery"))
    benchmarkization_readiness = _dict(
        report.benchmarkization_readiness
    ) or _benchmarkization_readiness(report)
    return {
        "preset": report.preset,
        "mode": report.mode,
        "source": report.source,
        "output_dir": report.output_dir,
        "benchmarkization_readiness": benchmarkization_readiness,
        "discovery": {
            "item_count": report.discovery_item_count,
            "requested_url_count": len(report.requested_urls),
            "requested_urls": list(report.requested_urls),
            "metadata": dict(report.discovery_metadata or {}),
        },
        "limits": {
            "max_sources": report.source_limit,
            "max_candidates": report.candidate_limit,
            "recipe_selection_mode": recipe_selection.get("mode", ""),
            "selected_recipes": list(_list(recipe_selection.get("selected_recipes"))),
            "recommended_recipe_count": _int(
                recipe_selection.get("recommended_count", 0)
            ),
            "source_limit_strategy": report.quality_summary.get(
                "source_limit_strategy", "all"
            ),
            "target_prefix": report.quality_summary.get("target_prefix", ""),
            "target_prefix_source": report.quality_summary.get(
                "target_prefix_source", "none"
            ),
            "candidate_limit_strategy": report.quality_summary.get(
                "candidate_limit_strategy", "all"
            ),
            "auto_dependency_sources": auto_dependency_sources,
            "dependency_source_count": report.quality_summary.get(
                "dependency_source_count", 0
            ),
            "dependency_max_depth": dependency_max_depth,
        },
        "actions": {
            "materialize_template": materialize_template,
            "run_benchmark": run_benchmark,
            "run_quality_gate": run_quality_gate,
            "run_showcase_lite": run_showcase_lite,
            "run_smoke_validation": run_smoke_validation,
            "run_repository_test_command": run_repository_test_command,
            "run_repository_test_environment_setup": run_repository_test_environment_setup,
            "run_repository_test_retry": run_repository_test_retry,
            "run_repository_test_retry_prerequisites": (
                run_repository_test_retry_prerequisites
            ),
            "auto_repository_test_retry": auto_repository_test_retry,
            "auto_repository_test_retry_max_risk": auto_repository_test_retry_max_risk,
            "auto_repository_test_retry_allowed_runners": [
                str(item) for item in (auto_repository_test_retry_allowed_runners or [])
            ],
            "checkout_repository_tests": checkout_repository_tests,
        },
        "repository_checkout": {
            "present": bool(repository_checkout),
            "status": repository_checkout.get("status") if repository_checkout else None,
            "reason": repository_checkout.get("reason") if repository_checkout else None,
            "checkout_method": repository_checkout.get("checkout_method")
            if repository_checkout
            else None,
            "checkout_path": repository_checkout.get("checkout_path")
            if repository_checkout
            else None,
            "timeout": repository_checkout_timeout,
            "depth": repository_checkout_depth,
        },
        "repository_checkout_sources": {
            "present": bool(repository_checkout_sources),
            "mode": checkout_source_metadata.get("mode")
            if repository_checkout_sources
            else None,
            "reason": checkout_source_metadata.get("reason")
            if repository_checkout_sources
            else None,
            "checkout_path": checkout_source_metadata.get("checkout_path")
            if repository_checkout_sources
            else None,
            "scanned_file_count": checkout_source_metadata.get("scanned_file_count")
            if repository_checkout_sources
            else 0,
            "included_file_count": checkout_source_metadata.get("included_file_count")
            if repository_checkout_sources
            else 0,
            "truncated": bool(checkout_source_metadata.get("truncated", False)),
        },
        "repository_config_snapshot": {
            "present": bool(repository_config_snapshot),
            "status": repository_config_snapshot.get("status")
            if repository_config_snapshot
            else None,
            "reason": repository_config_snapshot.get("reason")
            if repository_config_snapshot
            else None,
            "config_root": repository_config_snapshot.get("config_root")
            if repository_config_snapshot
            else None,
            "file_count": _int(repository_config_snapshot.get("file_count", 0)),
            "files": [
                str(item) for item in _list(repository_config_snapshot.get("files"))
            ],
        },
        "repository_test_command": {
            "present": bool(repository_test),
            "status": repository_test.get("status") if repository_test else None,
            "executed": bool(repository_test.get("executed", False)),
            "reason": repository_test.get("reason") if repository_test else None,
            "command": repository_test.get("command") if repository_test else None,
            "repository_test_root": str(repository_test_root)
            if repository_test_root is not None
            else None,
            "timeout": repository_test_timeout,
            "failure_overlay_candidate_limit": repository_test_failure_overlay_candidate_limit,
            "patch_validation_limit": repository_test_patch_validation_limit,
            "patch_generation_mode": repository_patch_generation_mode,
            "llm_patch_candidate_limit": repository_llm_patch_candidate_limit,
            "patch_candidate_variant_allowlist": [
                str(item)
                for item in (repository_patch_candidate_variant_allowlist or [])
                if str(item)
            ],
        },
        "repository_test_environment": {
            "present": bool(repository_environment),
            "status": repository_environment.get("status")
            if repository_environment
            else None,
            "reason": repository_environment.get("reason")
            if repository_environment
            else None,
            "recommended_install_command": repository_environment.get(
                "recommended_install_command"
            )
            if repository_environment
            else None,
            "test_module": repository_environment.get("test_module")
            if repository_environment
            else None,
            "test_tool_available": repository_environment.get(
                "test_tool_available"
            )
            if repository_environment
            else None,
        },
        "repository_test_environment_setup": {
            "present": bool(repository_environment_setup),
            "status": repository_environment_setup.get("status")
            if repository_environment_setup
            else None,
            "reason": repository_environment_setup.get("reason")
            if repository_environment_setup
            else None,
            "isolation_mode": repository_environment_setup.get("isolation_mode")
            if repository_environment_setup
            else None,
            "venv_path": repository_environment_setup.get("venv_path")
            if repository_environment_setup
            else None,
            "install_command_supported": bool(
                repository_environment_setup.get("install_command_supported", False)
            ),
            "install_requires_repository_root": bool(
                repository_environment_setup.get(
                    "install_requires_repository_root",
                    False,
                )
            ),
        },
        "repository_test_environment_setup_result": {
            "present": bool(repository_environment_setup_result),
            "status": repository_environment_setup_result.get("status")
            if repository_environment_setup_result
            else None,
            "executed": bool(
                repository_environment_setup_result.get("executed", False)
            ),
            "reason": repository_environment_setup_result.get("reason")
            if repository_environment_setup_result
            else None,
            "create_returncode": repository_environment_setup_result.get(
                "create_returncode"
            )
            if repository_environment_setup_result
            else None,
            "install_returncode": repository_environment_setup_result.get(
                "install_returncode"
            )
            if repository_environment_setup_result
            else None,
            "triggered_by": repository_environment_setup_result.get("triggered_by")
            if repository_environment_setup_result
            else None,
            "auto_retry_prerequisite": bool(
                repository_environment_setup_result.get(
                    "auto_retry_prerequisite",
                    False,
                )
            ),
            "timeout": repository_test_environment_setup_timeout,
        },
        "repository_test_execution_plan": {
            "present": bool(repository_execution_plan),
            "status": repository_execution_plan.get("status")
            if repository_execution_plan
            else None,
            "reason": repository_execution_plan.get("reason")
            if repository_execution_plan
            else None,
            "recommended_execution_command": repository_execution_plan.get(
                "recommended_execution_command"
            )
            if repository_execution_plan
            else None,
            "recommended_execution_level": repository_execution_plan.get(
                "recommended_execution_level"
            )
            if repository_execution_plan
            else None,
            "recommended_execution_risk": repository_execution_plan.get(
                "recommended_execution_risk"
            )
            if repository_execution_plan
            else None,
            "recommended_execution_runner": repository_execution_plan.get(
                "recommended_execution_runner"
            )
            if repository_execution_plan
            else None,
            "executable_now": bool(
                repository_execution_plan.get("executable_now", False)
            ),
            "selected_test_count": len(
                _list(repository_execution_plan.get("selected_test_paths"))
            )
            if repository_execution_plan
            else 0,
        },
        "repository_test_execution_result": {
            "present": bool(repository_execution_result),
            "status": repository_execution_result.get("status")
            if repository_execution_result
            else None,
            "executed": bool(repository_execution_result.get("executed", False)),
            "reason": repository_execution_result.get("reason")
            if repository_execution_result
            else None,
            "command": repository_execution_result.get("command")
            if repository_execution_result
            else None,
            "execution_level": repository_execution_result.get("execution_level")
            if repository_execution_result
            else None,
            "execution_risk": repository_execution_result.get("execution_risk")
            if repository_execution_result
            else None,
            "python_executable": repository_execution_result.get(
                "python_executable"
            )
            if repository_execution_result
            else None,
            "python_executable_source": repository_execution_result.get(
                "python_executable_source"
            )
            if repository_execution_result
            else None,
            "failure_category": repository_execution_result.get("failure_category")
            if repository_execution_result
            else None,
            "failure_signal": repository_execution_result.get("failure_signal")
            if repository_execution_result
            else None,
            "diagnostic_summary": repository_execution_result.get(
                "diagnostic_summary"
            )
            if repository_execution_result
            else None,
            "passed": _int(repository_execution_result.get("passed", 0)),
            "failed": _int(repository_execution_result.get("failed", 0)),
            "returncode": repository_execution_result.get("returncode")
            if repository_execution_result
            else None,
        },
        "repository_test_retry_plan": {
            "present": bool(repository_retry_plan),
            "status": repository_retry_plan.get("status")
            if repository_retry_plan
            else None,
            "reason": repository_retry_plan.get("reason")
            if repository_retry_plan
            else None,
            "retry_recommended": bool(
                repository_retry_plan.get("retry_recommended", False)
            ),
            "retry_strategy": repository_retry_plan.get("retry_strategy")
            if repository_retry_plan
            else None,
            "retry_command": repository_retry_plan.get("retry_command")
            if repository_retry_plan
            else None,
            "retry_level": repository_retry_plan.get("retry_level")
            if repository_retry_plan
            else None,
            "retry_risk": repository_retry_plan.get("retry_risk")
            if repository_retry_plan
            else None,
            "failure_category": repository_retry_plan.get("failure_category")
            if repository_retry_plan
            else None,
        },
        "repository_test_retry_execution_result": {
            "present": bool(repository_retry_execution_result),
            "status": repository_retry_execution_result.get("status")
            if repository_retry_execution_result
            else None,
            "executed": bool(
                repository_retry_execution_result.get("executed", False)
            ),
            "reason": repository_retry_execution_result.get("reason")
            if repository_retry_execution_result
            else None,
            "retry_enabled": bool(
                repository_retry_execution_result.get("retry_enabled", False)
            ),
            "retry_strategy": repository_retry_execution_result.get(
                "retry_strategy"
            )
            if repository_retry_execution_result
            else None,
            "retry_command": repository_retry_execution_result.get("retry_command")
            if repository_retry_execution_result
            else None,
            "passed": _int(repository_retry_execution_result.get("passed", 0)),
            "failed": _int(repository_retry_execution_result.get("failed", 0)),
            "returncode": repository_retry_execution_result.get("returncode")
            if repository_retry_execution_result
            else None,
            "retry_setup_prerequisite_required": bool(
                repository_retry_execution_result.get(
                    "retry_setup_prerequisite_required",
                    False,
                )
            ),
            "retry_setup_prerequisite_satisfied": bool(
                repository_retry_execution_result.get(
                    "retry_setup_prerequisite_satisfied",
                    False,
                )
            ),
            "retry_setup_prerequisite_status": repository_retry_execution_result.get(
                "retry_setup_prerequisite_status"
            )
            if repository_retry_execution_result
            else None,
            "retry_setup_prerequisite_auto_executed": bool(
                repository_retry_execution_result.get(
                    "retry_setup_prerequisite_auto_executed",
                    False,
                )
            ),
        },
        "repository_test_setup_doctor": {
            "present": bool(repository_setup_doctor),
            "status": repository_setup_doctor.get("status")
            if repository_setup_doctor
            else None,
            "blocker": repository_setup_doctor.get("blocker")
            if repository_setup_doctor
            else None,
            "score": _float(repository_setup_doctor.get("score", 0.0)),
            "next_action": repository_setup_doctor.get("next_action")
            if repository_setup_doctor
            else None,
            "checks": [
                dict(check)
                for check in _list(repository_setup_doctor.get("checks"))
                if isinstance(check, dict)
            ],
        },
        "repository_test_pytest_plugin_repair": {
            "present": bool(repository_pytest_plugin_repair),
            "status": repository_pytest_plugin_repair.get("status")
            if repository_pytest_plugin_repair
            else None,
            "executed": bool(
                repository_pytest_plugin_repair.get("executed", False)
            ),
            "reason": repository_pytest_plugin_repair.get("reason")
            if repository_pytest_plugin_repair
            else None,
            "fixture": repository_pytest_plugin_repair.get("fixture")
            if repository_pytest_plugin_repair
            else None,
            "plugin_package": repository_pytest_plugin_repair.get("plugin_package")
            if repository_pytest_plugin_repair
            else None,
            "plugin_requirement": repository_pytest_plugin_repair.get(
                "plugin_requirement"
            )
            if repository_pytest_plugin_repair
            else None,
            "returncode": repository_pytest_plugin_repair.get("returncode")
            if repository_pytest_plugin_repair
            else None,
        },
        "repository_test_pytest_plugin_repair_retry_execution_result": {
            "present": bool(repository_pytest_plugin_repair_retry_execution_result),
            "status": repository_pytest_plugin_repair_retry_execution_result.get(
                "status"
            )
            if repository_pytest_plugin_repair_retry_execution_result
            else None,
            "executed": bool(
                repository_pytest_plugin_repair_retry_execution_result.get(
                    "executed",
                    False,
                )
            ),
            "reason": repository_pytest_plugin_repair_retry_execution_result.get(
                "reason"
            )
            if repository_pytest_plugin_repair_retry_execution_result
            else None,
            "retry_command": repository_pytest_plugin_repair_retry_execution_result.get(
                "retry_command"
            )
            if repository_pytest_plugin_repair_retry_execution_result
            else None,
            "passed": _int(
                repository_pytest_plugin_repair_retry_execution_result.get(
                    "passed",
                    0,
                )
            ),
            "failed": _int(
                repository_pytest_plugin_repair_retry_execution_result.get(
                    "failed",
                    0,
                )
            ),
            "returncode": repository_pytest_plugin_repair_retry_execution_result.get(
                "returncode"
            )
            if repository_pytest_plugin_repair_retry_execution_result
            else None,
        },
        "repository_test_timeout_narrowing": {
            "present": bool(repository_timeout_narrowing),
            "status": repository_timeout_narrowing.get("status")
            if repository_timeout_narrowing
            else None,
            "executed": bool(repository_timeout_narrowing.get("executed", False)),
            "reason": repository_timeout_narrowing.get("reason")
            if repository_timeout_narrowing
            else None,
            "timeout_command": repository_timeout_narrowing.get("timeout_command")
            if repository_timeout_narrowing
            else None,
            "selected_command": repository_timeout_narrowing.get(
                "selected_command"
            )
            if repository_timeout_narrowing
            else None,
            "selected_failure_category": repository_timeout_narrowing.get(
                "selected_failure_category"
            )
            if repository_timeout_narrowing
            else None,
            "attempt_count": _int(repository_timeout_narrowing.get("attempt_count", 0)),
        },
        "repository_test_dynamic_evidence": {
            "present": bool(repository_dynamic_evidence),
            "status": repository_dynamic_evidence.get("status")
            if repository_dynamic_evidence
            else None,
            "reason": repository_dynamic_evidence.get("reason")
            if repository_dynamic_evidence
            else None,
            "evidence_level": repository_dynamic_evidence.get("evidence_level")
            if repository_dynamic_evidence
            else None,
            "source": repository_dynamic_evidence.get("source")
            if repository_dynamic_evidence
            else None,
            "failing_test_count": _int(
                repository_dynamic_evidence.get("failing_test_count", 0)
            ),
            "failed_test_count": _int(
                repository_dynamic_evidence.get("failed_test_count", 0)
            ),
            "passed_test_count": _int(
                repository_dynamic_evidence.get("passed_test_count", 0)
            ),
            "usable_for_localization": bool(
                repository_dynamic_evidence.get("usable_for_localization", False)
            ),
            "usable_for_regression_validation": bool(
                repository_dynamic_evidence.get(
                    "usable_for_regression_validation",
                    False,
                )
            ),
            "usable_for_patch_validation": bool(
                repository_dynamic_evidence.get(
                    "usable_for_patch_validation",
                    False,
                )
            ),
            "recommended_validation_command": repository_dynamic_evidence.get(
                "recommended_validation_command"
            )
            if repository_dynamic_evidence
            else None,
        },
        "repository_test_effective_execution_result": (
            repository_effective_execution_result
        ),
        "repository_test_failure_overlay": {
            "present": bool(repository_failure_overlay),
            "status": repository_failure_overlay.get("status")
            if repository_failure_overlay
            else None,
            "reason": repository_failure_overlay.get("reason")
            if repository_failure_overlay
            else None,
            "overlay_root": repository_failure_overlay.get("overlay_root")
            if repository_failure_overlay
            else None,
            "analysis_scope": _dict(repository_failure_overlay.get("analysis_scope"))
            if repository_failure_overlay
            else {},
            "analysis_scoped": bool(
                _dict(repository_failure_overlay.get("analysis_scope")).get(
                    "enabled",
                    False,
                )
            )
            if repository_failure_overlay
            else False,
            "analysis_file_count": _int(
                _dict(repository_failure_overlay.get("analysis_scope")).get(
                    "existing_file_count",
                    0,
                )
            )
            if repository_failure_overlay
            else 0,
            "analysis_files": _list(
                _dict(repository_failure_overlay.get("analysis_scope")).get(
                    "existing_files"
                )
            )
            if repository_failure_overlay
            else [],
            "missing_analysis_path_count": _int(
                _dict(repository_failure_overlay.get("analysis_scope")).get(
                    "missing_path_count",
                    0,
                )
            )
            if repository_failure_overlay
            else 0,
            "missing_analysis_paths": _list(
                _dict(repository_failure_overlay.get("analysis_scope")).get(
                    "missing_paths"
                )
            )
            if repository_failure_overlay
            else [],
            "static_finding_count": _int(
                repository_failure_overlay.get("static_finding_count", 0)
            ),
            "supported_candidate_count": _int(
                repository_failure_overlay.get("supported_candidate_count", 0)
            ),
            "attempted_case_count": _int(
                repository_failure_overlay.get("attempted_case_count", 0)
            ),
            "strategy_policy": _dict(
                repository_failure_overlay.get("strategy_summary")
            ).get("policy")
            if repository_failure_overlay
            else None,
            "candidate_rule_counts": _dict(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "candidate_rule_counts"
                )
            )
            if repository_failure_overlay
            else {},
            "attempted_rule_counts": _dict(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "attempted_rule_counts"
                )
            )
            if repository_failure_overlay
            else {},
            "triggered_rule_counts": _dict(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "triggered_rule_counts"
                )
            )
            if repository_failure_overlay
            else {},
            "candidate_rejection_count": _int(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "candidate_rejection_count",
                    0,
                )
            )
            if repository_failure_overlay
            else 0,
            "candidate_rejection_counts": _dict(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "candidate_rejection_counts"
                )
            )
            if repository_failure_overlay
            else {},
            "candidate_rejection_rule_counts": _dict(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "candidate_rejection_rule_counts"
                )
            )
            if repository_failure_overlay
            else {},
            "candidate_rejection_examples": _list(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "candidate_rejection_examples"
                )
            )
            if repository_failure_overlay
            else [],
            "dominant_candidate_rejection_reason": str(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "dominant_candidate_rejection_reason"
                )
                or ""
            )
            if repository_failure_overlay
            else "",
            "dominant_candidate_rejection_count": _int(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "dominant_candidate_rejection_count",
                    0,
                )
            )
            if repository_failure_overlay
            else 0,
            "candidate_rejection_recommendations": _list(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "candidate_rejection_recommendations"
                )
            )
            if repository_failure_overlay
            else [],
            "next_overlay_extension": _dict(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "next_overlay_extension"
                )
            )
            if repository_failure_overlay
            else {},
            "next_actionable_overlay_extension": _dict(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "next_actionable_overlay_extension"
                )
            )
            if repository_failure_overlay
            else {},
            "selected_candidate_rank": _int(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "selected_candidate_rank",
                    0,
                )
            )
            if repository_failure_overlay
            else 0,
            "selected_rule_id": _dict(
                repository_failure_overlay.get("selected_case")
            ).get("rule_id")
            if repository_failure_overlay
            else None,
            "selected_function": _dict(
                repository_failure_overlay.get("selected_case")
            ).get("function_name")
            if repository_failure_overlay
            else None,
            "public_api_evidence": _dict(
                _dict(repository_failure_overlay.get("selected_case")).get(
                    "public_api_evidence"
                )
            )
            if repository_failure_overlay
            else {},
            "overlay_case_context": _dict(
                _dict(repository_failure_overlay.get("dynamic_evidence")).get(
                    "overlay_case_context"
                )
            )
            if repository_failure_overlay
            else {},
            "selected_score": _float(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "selected_score",
                    0.0,
                )
            )
            if repository_failure_overlay
            else 0.0,
            "average_candidate_score": _float(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "average_candidate_score",
                    0.0,
                )
            )
            if repository_failure_overlay
            else 0.0,
            "selected_score_breakdown": _dict(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "selected_score_breakdown"
                )
            )
            if repository_failure_overlay
            else {},
            "candidate_score_preview": _list(
                _dict(repository_failure_overlay.get("strategy_summary")).get(
                    "candidate_score_preview"
                )
            )
            if repository_failure_overlay
            else [],
            "recommended_validation_command": repository_failure_overlay.get(
                "recommended_validation_command"
            )
            if repository_failure_overlay
            else None,
            "dynamic_evidence_level": _dict(
                repository_failure_overlay.get("dynamic_evidence")
            ).get("evidence_level")
            if repository_failure_overlay
            else None,
        },
        "repository_test_analysis_route": repository_analysis_route,
        "repository_test_fault_localization": {
            "present": bool(repository_fault_localization),
            "status": repository_fault_localization.get("status")
            if repository_fault_localization
            else None,
            "reason": repository_fault_localization.get("reason")
            if repository_fault_localization
            else None,
            "ranking_count": _int(
                repository_fault_localization.get("ranking_count", 0)
            ),
            "top_function": repository_fault_localization.get("top_function")
            if repository_fault_localization
            else None,
            "top_score": repository_fault_localization.get("top_score")
            if repository_fault_localization
            else 0.0,
            "matched_failed_test_count": _int(
                repository_fault_localization.get("matched_failed_test_count", 0)
            ),
            "unmatched_failed_test_count": _int(
                repository_fault_localization.get("unmatched_failed_test_count", 0)
            ),
            "public_api_evidence": _dict(
                repository_fault_localization.get("public_api_evidence")
            )
            if repository_fault_localization
            else {},
            "overlay_case_context": _dict(
                repository_fault_localization.get("overlay_case_context")
            )
            if repository_fault_localization
            else {},
        },
        "repository_test_patch_candidates": {
            "present": bool(repository_patch_candidates),
            "status": repository_patch_candidates.get("status")
            if repository_patch_candidates
            else None,
            "reason": repository_patch_candidates.get("reason")
            if repository_patch_candidates
            else None,
            "candidate_count": _int(
                repository_patch_candidates.get("candidate_count", 0)
            ),
            "target_function_count": _int(
                repository_patch_candidates.get("target_function_count", 0)
            ),
            "recommended_validation_command": repository_patch_candidates.get(
                "recommended_validation_command"
            )
            if repository_patch_candidates
            else None,
            "recommended_pytest_args": list(
                _list(repository_patch_candidates.get("recommended_pytest_args"))
            ),
        },
        "repository_test_patch_validation": {
            "present": bool(repository_patch_validation),
            "status": repository_patch_validation.get("status")
            if repository_patch_validation
            else None,
            "reason": repository_patch_validation.get("reason")
            if repository_patch_validation
            else None,
            "candidate_count": _int(
                repository_patch_validation.get("candidate_count", 0)
            ),
            "validation_limit": _int(
                repository_patch_validation.get("validation_limit", 0)
            ),
            "executed_count": _int(
                repository_patch_validation.get("executed_count", 0)
            ),
            "success_count": _int(
                repository_patch_validation.get("success_count", 0)
            ),
            "repair_ready": bool(
                repository_patch_validation.get("repair_ready", False)
            ),
            "repair_validation_scope": repository_patch_validation.get(
                "repair_validation_scope"
            )
            if repository_patch_validation
            else None,
            "regression_ready": bool(
                repository_patch_validation.get("regression_ready", False)
            ),
            "regression_validation_status": repository_regression_validation.get(
                "status"
            )
            if repository_regression_validation
            else None,
            "regression_validation_reason": repository_regression_validation.get(
                "reason"
            )
            if repository_regression_validation
            else None,
            "regression_validation_command": repository_regression_validation.get(
                "validation_command"
            )
            if repository_regression_validation
            else None,
            "regression_validation_pytest_args": list(
                _list(repository_regression_validation.get("pytest_args"))
            ),
            "regression_validation_passed": _int(
                repository_regression_validation.get("passed", 0)
            ),
            "regression_validation_failed": _int(
                repository_regression_validation.get("failed", 0)
            ),
            "requested_reflection_mode": repository_test_reflection_mode,
            "requested_reflection_rounds": repository_test_reflection_rounds,
            "requested_reflection_width": repository_test_reflection_width,
            "reflection_enabled": bool(
                repository_patch_validation.get("reflection_enabled", False)
            ),
            "reflection_mode": repository_patch_validation.get("reflection_mode")
            if repository_patch_validation
            else None,
            "reflection_refiner_status": repository_patch_validation.get(
                "reflection_refiner_status"
            )
            if repository_patch_validation
            else None,
            "reflection_refiner_reason": repository_patch_validation.get(
                "reflection_refiner_reason"
            )
            if repository_patch_validation
            else None,
            "reflection_rounds": _int(
                repository_patch_validation.get("reflection_rounds", 0)
            ),
            "reflection_candidate_count": _int(
                repository_patch_validation.get("reflection_candidate_count", 0)
            ),
            "successful_reflection_candidate_count": _int(
                repository_patch_validation.get(
                    "successful_reflection_candidate_count",
                    0,
                )
            ),
            "max_depth_executed": _int(
                repository_patch_validation.get("max_depth_executed", 0)
            ),
            "best_candidate_id": repository_patch_validation.get(
                "best_candidate_id"
            )
            if repository_patch_validation
            else None,
            "best_candidate_rule_id": repository_patch_validation.get(
                "best_candidate_rule_id"
            )
            if repository_patch_validation
            else None,
            "best_candidate_variant": repository_patch_validation.get(
                "best_candidate_variant"
            )
            if repository_patch_validation
            else None,
            "best_candidate_success": bool(
                repository_patch_validation.get("best_candidate_success", False)
            ),
            "best_patch_candidate_id": repository_best_patch.get("candidate_id")
            if repository_best_patch
            else None,
            "best_patch_relative_file_path": repository_best_patch.get(
                "relative_file_path"
            )
            if repository_best_patch
            else None,
            "best_patch_rule_id": repository_best_patch.get("rule_id")
            if repository_best_patch
            else None,
            "best_patch_variant": repository_best_patch.get("variant")
            if repository_best_patch
            else None,
            "best_patch_depth": _int(repository_best_patch.get("depth", 0)),
            "best_patch_has_diff": bool(
                str(repository_best_patch.get("diff") or "")
            ),
            "failure_type_counts": dict(
                _dict(repository_patch_validation.get("failure_type_counts"))
            ),
            "patch_judge_mode": repository_patch_validation.get("patch_judge_mode")
            if repository_patch_validation
            else None,
            "patch_judge_status": repository_patch_validation.get("patch_judge_status")
            if repository_patch_validation
            else None,
            "patch_judge_reason": repository_patch_validation.get("patch_judge_reason")
            if repository_patch_validation
            else None,
            "patch_judge_enabled": bool(
                repository_patch_validation.get("patch_judge_enabled", False)
            ),
            "patch_judge_candidate_count": _int(
                repository_patch_validation.get("patch_judge_candidate_count", 0)
            ),
            "patch_judge_verdict_counts": dict(
                _dict(repository_patch_validation.get("patch_judge_verdict_counts"))
            ),
            "patch_judge_agreement_counts": dict(
                _dict(repository_patch_validation.get("patch_judge_agreement_counts"))
            ),
            "patch_judge_authority": repository_patch_validation.get(
                "patch_judge_authority"
            )
            if repository_patch_validation
            else None,
            "patch_judge_config_audit": _dict(
                repository_patch_validation.get("patch_judge_config_audit")
            ),
        },
        "repository_test_repair_summary": {
            "present": bool(repository_repair_summary),
            "status": repository_repair_summary.get("status")
            if repository_repair_summary
            else None,
            "reason": repository_repair_summary.get("reason")
            if repository_repair_summary
            else None,
            "conclusion": repository_repair_summary.get("conclusion")
            if repository_repair_summary
            else None,
            "repair_ready": bool(
                repository_repair_summary.get("repair_ready", False)
            ),
            "repair_validation_scope": repository_repair_summary.get(
                "repair_validation_scope"
            )
            if repository_repair_summary
            else None,
            "patch_path": repository_repair_summary.get("patch_path")
            if repository_repair_summary
            else None,
            "patch_path_present": bool(
                repository_repair_summary.get("patch_path_present", False)
            ),
        },
        "benchmark": {
            "benchmark_output_dir": str(benchmark_output_dir)
            if benchmark_output_dir is not None
            else None,
            "patch_mode": patch_mode,
            "judge_mode": judge_mode,
            "patch_judge_mode": patch_judge_mode,
            "llm_score_mode": llm_score_mode,
            "use_dynamic_coverage": use_dynamic_coverage,
            "benchmark_run_present": report.benchmark_run is not None,
        },
        "quality_gate": {
            "present": report.quality_gate is not None,
            "passed": _dict(report.quality_gate).get("passed")
            if report.quality_gate is not None
            else None,
            "thresholds": quality_gate_thresholds.to_dict(),
        },
        "smoke_validation": {
            "present": report.smoke_validation is not None,
            "passed": _dict(report.smoke_validation).get("passed")
            if report.smoke_validation is not None
            else None,
        },
        "resolved_artifacts": dict(report.output_paths),
    }


def _repository_test_effective_execution_result_summary(
    *,
    repository_execution_result: dict[str, Any],
    repository_retry_execution_result: dict[str, Any],
    repository_pytest_plugin_repair_retry_execution_result: dict[str, Any],
    repository_timeout_narrowing: dict[str, Any],
    repository_dynamic_evidence: dict[str, Any],
) -> dict[str, Any]:
    candidates = [
        (
            "dynamic_evidence",
            _dict(repository_dynamic_evidence.get("selected_execution")),
        ),
        (
            "timeout_narrowing",
            _dict(repository_timeout_narrowing.get("selected_execution")),
        ),
        (
            "pytest_plugin_repair_retry_execution_result",
            repository_pytest_plugin_repair_retry_execution_result,
        ),
        ("retry_execution_result", repository_retry_execution_result),
        ("planned_execution_result", repository_execution_result),
    ]
    for source, execution in candidates:
        if not execution:
            continue
        return {
            "present": True,
            "source": source,
            "status": execution.get("status"),
            "executed": bool(execution.get("executed", False)),
            "reason": execution.get("reason"),
            "command": (
                execution.get("command")
                or repository_dynamic_evidence.get("recommended_validation_command")
            ),
            "execution_level": execution.get("execution_level"),
            "execution_risk": execution.get("execution_risk"),
            "failure_category": execution.get("failure_category"),
            "failure_signal": execution.get("failure_signal"),
            "diagnostic_summary": execution.get("diagnostic_summary"),
            "passed": _int(execution.get("passed", 0)),
            "failed": _int(execution.get("failed", 0)),
            "returncode": execution.get("returncode"),
        }
    return {"present": False}


def render_onboarding_run_config_markdown(config: dict[str, Any]) -> str:
    discovery = _dict(config.get("discovery"))
    discovery_metadata = _dict(discovery.get("metadata"))
    limits = _dict(config.get("limits"))
    actions = _dict(config.get("actions"))
    benchmarkization = _dict(config.get("benchmarkization_readiness"))
    benchmark = _dict(config.get("benchmark"))
    quality_gate = _dict(config.get("quality_gate"))
    smoke_validation = _dict(config.get("smoke_validation"))
    repository_checkout = _dict(config.get("repository_checkout"))
    repository_checkout_sources = _dict(config.get("repository_checkout_sources"))
    repository_config_snapshot = _dict(config.get("repository_config_snapshot"))
    repository_test = _dict(config.get("repository_test_command"))
    repository_environment = _dict(config.get("repository_test_environment"))
    repository_environment_setup = _dict(
        config.get("repository_test_environment_setup")
    )
    repository_environment_setup_result = _dict(
        config.get("repository_test_environment_setup_result")
    )
    repository_execution_plan = _dict(config.get("repository_test_execution_plan"))
    repository_execution_result = _dict(
        config.get("repository_test_execution_result")
    )
    repository_retry_plan = _dict(config.get("repository_test_retry_plan"))
    repository_retry_execution_result = _dict(
        config.get("repository_test_retry_execution_result")
    )
    repository_setup_doctor = _dict(config.get("repository_test_setup_doctor"))
    repository_dynamic_evidence = _dict(
        config.get("repository_test_dynamic_evidence")
    )
    repository_effective_execution = _dict(
        config.get("repository_test_effective_execution_result")
    )
    repository_analysis_route = _dict(
        config.get("repository_test_analysis_route")
    )
    repository_failure_overlay = _dict(
        config.get("repository_test_failure_overlay")
    )
    repository_fault_localization = _dict(
        config.get("repository_test_fault_localization")
    )
    repository_patch_candidates = _dict(
        config.get("repository_test_patch_candidates")
    )
    repository_patch_validation = _dict(
        config.get("repository_test_patch_validation")
    )
    repository_repair_summary = _dict(config.get("repository_test_repair_summary"))
    lines = [
        "# GitHub Onboarding Run Config",
        "",
        f"- Preset: `{config.get('preset', 'manual')}`",
        f"- Mode: `{config.get('mode', '')}`",
        f"- Source: `{config.get('source', '')}`",
        f"- Output Dir: `{config.get('output_dir', '')}`",
        "",
        "## Discovery",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| item_count | {_markdown_cell(discovery.get('item_count', 0))} |",
        (
            "| requested_url_count | "
            f"{_markdown_cell(discovery.get('requested_url_count', 0))} |"
        ),
        f"| owner | {_markdown_cell(discovery_metadata.get('owner', ''))} |",
        f"| repo | {_markdown_cell(discovery_metadata.get('repo', ''))} |",
        f"| ref | {_markdown_cell(discovery_metadata.get('ref', ''))} |",
        f"| requested_ref | {_markdown_cell(discovery_metadata.get('requested_ref', ''))} |",
        f"| ref_source | {_markdown_cell(discovery_metadata.get('ref_source', ''))} |",
        f"| recursive | {_markdown_cell(discovery_metadata.get('recursive', ''))} |",
        "",
        "## Effective Limits",
        "",
        "| Setting | Value |",
        "| --- | --- |",
        f"| max_sources | {_markdown_cell(limits.get('max_sources') or 'none')} |",
        f"| max_candidates | {_markdown_cell(limits.get('max_candidates') or 'none')} |",
        f"| recipe_selection_mode | {_markdown_cell(limits.get('recipe_selection_mode'))} |",
        f"| selected_recipes | {_markdown_cell(', '.join(str(item) for item in _list(limits.get('selected_recipes'))))} |",
        f"| recommended_recipe_count | {_markdown_cell(limits.get('recommended_recipe_count'))} |",
        f"| target_prefix | {_markdown_cell(limits.get('target_prefix') or 'none')} |",
        f"| target_prefix_source | {_markdown_cell(limits.get('target_prefix_source') or 'none')} |",
        f"| auto_dependency_sources | {_markdown_cell(limits.get('auto_dependency_sources'))} |",
        f"| dependency_source_count | {_markdown_cell(limits.get('dependency_source_count'))} |",
        f"| dependency_max_depth | {_markdown_cell(limits.get('dependency_max_depth'))} |",
        f"| source_limit_strategy | {_markdown_cell(limits.get('source_limit_strategy', 'all'))} |",
        f"| candidate_limit_strategy | {_markdown_cell(limits.get('candidate_limit_strategy', 'all'))} |",
        "",
        "## Actions",
        "",
        "| Action | Enabled |",
        "| --- | --- |",
    ]
    for name in (
        "materialize_template",
        "run_benchmark",
        "run_quality_gate",
        "run_showcase_lite",
        "run_smoke_validation",
        "run_repository_test_command",
        "run_repository_test_environment_setup",
        "run_repository_test_retry",
        "auto_repository_test_retry",
        "checkout_repository_tests",
    ):
        lines.append(f"| {name} | {str(bool(actions.get(name))).lower()} |")
    lines.append(
        "| auto_repository_test_retry_max_risk | "
        f"{_markdown_cell(actions.get('auto_repository_test_retry_max_risk') or 'none')} |"
    )
    lines.append(
        "| auto_repository_test_retry_allowed_runners | "
        f"{_markdown_cell(', '.join(str(item) for item in _list(actions.get('auto_repository_test_retry_allowed_runners'))) or 'none')} |"
    )
    if benchmarkization:
        lines.extend(
            [
                "",
                "## Benchmarkization Readiness",
                "",
                f"- Status: `{_markdown_cell(benchmarkization.get('status', 'unknown'))}`",
                f"- Stage: `{_markdown_cell(benchmarkization.get('stage', 'unknown'))}`",
                f"- Ready: {str(bool(benchmarkization.get('ready', False))).lower()}",
                f"- Benchmark Cases: {_markdown_cell(benchmarkization.get('benchmark_cases', 0))}",
                (
                    "- Repository Test Evidence: "
                    f"`{_markdown_cell(benchmarkization.get('repository_test_evidence_status') or 'not_started')}`"
                ),
                (
                    "- Blocking Reasons: "
                    f"`{_markdown_cell(', '.join(str(item) for item in _list(benchmarkization.get('blocking_reasons'))) or 'none')}`"
                ),
            ]
        )
        remediation_plan = _dict(benchmarkization.get("remediation_plan"))
        remediation_actions = _list(remediation_plan.get("actions"))
        if remediation_actions:
            lines.extend(
                [
                    "",
                    "### Remediation Plan",
                    "",
                    "| Action | Auto | Command |",
                    "| --- | --- | --- |",
                ]
            )
            for action_value in remediation_actions:
                action = _dict(action_value)
                lines.append(
                    "| "
                    f"{_markdown_cell(action.get('action_id', ''))} | "
                    f"{str(bool(action.get('auto_runnable', False))).lower()} | "
                    f"`{_markdown_cell(action.get('command') or 'none')}` |"
                )
    lines.extend(
        [
            "",
            "## Benchmark Settings",
            "",
            "| Setting | Value |",
            "| --- | --- |",
        ]
    )
    for name in (
        "patch_mode",
        "judge_mode",
        "patch_judge_mode",
        "llm_score_mode",
        "use_dynamic_coverage",
        "benchmark_run_present",
    ):
        lines.append(f"| {name} | {_markdown_cell(benchmark.get(name))} |")
    lines.extend(
        [
            "",
            "## Quality Gate",
            "",
            f"- Present: {str(bool(quality_gate.get('present'))).lower()}",
            f"- Passed: {_markdown_cell(quality_gate.get('passed'))}",
            "",
            "## Smoke Validation",
            "",
            f"- Present: {str(bool(smoke_validation.get('present'))).lower()}",
            f"- Passed: {_markdown_cell(smoke_validation.get('passed'))}",
            "",
            "## Repository Test Command",
            "",
            "### Repository Checkout",
            "",
            f"- Present: {str(bool(repository_checkout.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_checkout.get('status'))}`",
            f"- Reason: `{_markdown_cell(repository_checkout.get('reason'))}`",
            f"- Method: `{_markdown_cell(repository_checkout.get('checkout_method'))}`",
            f"- Checkout Path: `{_markdown_cell(repository_checkout.get('checkout_path'))}`",
            f"- Timeout: {_markdown_cell(repository_checkout.get('timeout'))}",
            f"- Depth: {_markdown_cell(repository_checkout.get('depth'))}",
            "",
            "### Repository Checkout Sources",
            "",
            f"- Present: {str(bool(repository_checkout_sources.get('present'))).lower()}",
            f"- Mode: `{_markdown_cell(repository_checkout_sources.get('mode'))}`",
            f"- Reason: `{_markdown_cell(repository_checkout_sources.get('reason'))}`",
            f"- Checkout Path: `{_markdown_cell(repository_checkout_sources.get('checkout_path'))}`",
            f"- Scanned Files: {_markdown_cell(repository_checkout_sources.get('scanned_file_count'))}",
            f"- Included Files: {_markdown_cell(repository_checkout_sources.get('included_file_count'))}",
            f"- Truncated: {str(bool(repository_checkout_sources.get('truncated'))).lower()}",
            "",
            "### Config Snapshot",
            "",
            f"- Present: {str(bool(repository_config_snapshot.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_config_snapshot.get('status'))}`",
            f"- Reason: `{_markdown_cell(repository_config_snapshot.get('reason'))}`",
            f"- Config Root: `{_markdown_cell(repository_config_snapshot.get('config_root'))}`",
            f"- Files: {_markdown_cell(repository_config_snapshot.get('file_count'))}",
            "",
            "### Command Validation",
            "",
            f"- Present: {str(bool(repository_test.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_test.get('status'))}`",
            f"- Executed: {str(bool(repository_test.get('executed'))).lower()}",
            f"- Reason: `{_markdown_cell(repository_test.get('reason'))}`",
            f"- Command: `{_markdown_cell(repository_test.get('command'))}`",
            f"- Repository Test Root: `{_markdown_cell(repository_test.get('repository_test_root'))}`",
            f"- Timeout: {_markdown_cell(repository_test.get('timeout'))}",
            "",
            "### Environment Plan",
            "",
            f"- Present: {str(bool(repository_environment.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_environment.get('status'))}`",
            f"- Reason: `{_markdown_cell(repository_environment.get('reason'))}`",
            (
                "- Recommended Install Command: "
                f"`{_markdown_cell(repository_environment.get('recommended_install_command') or 'none')}`"
            ),
            f"- Test Module: `{_markdown_cell(repository_environment.get('test_module') or 'none')}`",
            f"- Test Tool Available: `{_markdown_cell(repository_environment.get('test_tool_available'))}`",
            "",
            "### Environment Setup Plan",
            "",
            f"- Present: {str(bool(repository_environment_setup.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_environment_setup.get('status'))}`",
            f"- Reason: `{_markdown_cell(repository_environment_setup.get('reason'))}`",
            f"- Isolation Mode: `{_markdown_cell(repository_environment_setup.get('isolation_mode') or 'none')}`",
            f"- Venv Path: `{_markdown_cell(repository_environment_setup.get('venv_path') or 'none')}`",
            (
                "- Install Command Supported: "
                f"{str(bool(repository_environment_setup.get('install_command_supported'))).lower()}"
            ),
            (
                "- Install Requires Repository Root: "
                f"{str(bool(repository_environment_setup.get('install_requires_repository_root'))).lower()}"
            ),
            "",
            "### Environment Setup Result",
            "",
            f"- Present: {str(bool(repository_environment_setup_result.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_environment_setup_result.get('status'))}`",
            f"- Executed: {str(bool(repository_environment_setup_result.get('executed'))).lower()}",
            f"- Reason: `{_markdown_cell(repository_environment_setup_result.get('reason'))}`",
            f"- Create Return Code: {_markdown_cell(repository_environment_setup_result.get('create_returncode'))}",
            f"- Install Return Code: {_markdown_cell(repository_environment_setup_result.get('install_returncode'))}",
            f"- Timeout: {_markdown_cell(repository_environment_setup_result.get('timeout'))}",
            "",
            "### Execution Plan",
            "",
            f"- Present: {str(bool(repository_execution_plan.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_execution_plan.get('status'))}`",
            f"- Reason: `{_markdown_cell(repository_execution_plan.get('reason'))}`",
            (
                "- Recommended Execution Command: "
                f"`{_markdown_cell(repository_execution_plan.get('recommended_execution_command') or 'none')}`"
            ),
            (
                "- Recommended Execution Level: "
                f"`{_markdown_cell(repository_execution_plan.get('recommended_execution_level') or 'none')}`"
            ),
            (
                "- Recommended Execution Risk: "
                f"`{_markdown_cell(repository_execution_plan.get('recommended_execution_risk') or 'none')}`"
            ),
            f"- Executable Now: {str(bool(repository_execution_plan.get('executable_now'))).lower()}",
            f"- Selected Test Count: {_markdown_cell(repository_execution_plan.get('selected_test_count'))}",
            "",
            "### Setup Doctor",
            "",
            f"- Present: {str(bool(repository_setup_doctor.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_setup_doctor.get('status') or 'none')}`",
            f"- Blocker: `{_markdown_cell(repository_setup_doctor.get('blocker') or 'none')}`",
            f"- Score: {_float(repository_setup_doctor.get('score', 0.0)):.4f}",
            (
                "- Next Action: "
                f"{_markdown_cell(repository_setup_doctor.get('next_action') or 'none')}"
            ),
            "",
            "### Planned Execution Result",
            "",
            f"- Present: {str(bool(repository_execution_result.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_execution_result.get('status'))}`",
            f"- Executed: {str(bool(repository_execution_result.get('executed'))).lower()}",
            f"- Reason: `{_markdown_cell(repository_execution_result.get('reason'))}`",
            f"- Command: `{_markdown_cell(repository_execution_result.get('command'))}`",
            f"- Execution Level: `{_markdown_cell(repository_execution_result.get('execution_level'))}`",
            f"- Execution Risk: `{_markdown_cell(repository_execution_result.get('execution_risk'))}`",
            f"- Python Executable: `{_markdown_cell(repository_execution_result.get('python_executable') or 'none')}`",
            (
                "- Python Executable Source: "
                f"`{_markdown_cell(repository_execution_result.get('python_executable_source') or 'none')}`"
            ),
            (
                "- Failure Category: "
                f"`{_markdown_cell(repository_execution_result.get('failure_category') or 'none')}`"
            ),
            (
                "- Failure Signal: "
                f"`{_markdown_cell(repository_execution_result.get('failure_signal') or 'none')}`"
            ),
            (
                "- Diagnostic Summary: "
                f"{_markdown_cell(repository_execution_result.get('diagnostic_summary') or 'none')}"
            ),
            f"- Return Code: {_markdown_cell(repository_execution_result.get('returncode'))}",
            f"- Passed: {_markdown_cell(repository_execution_result.get('passed'))}",
            f"- Failed: {_markdown_cell(repository_execution_result.get('failed'))}",
            "",
            "### Retry Plan",
            "",
            f"- Present: {str(bool(repository_retry_plan.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_retry_plan.get('status'))}`",
            f"- Reason: `{_markdown_cell(repository_retry_plan.get('reason'))}`",
            f"- Retry Recommended: {str(bool(repository_retry_plan.get('retry_recommended'))).lower()}",
            f"- Retry Strategy: `{_markdown_cell(repository_retry_plan.get('retry_strategy') or 'none')}`",
            f"- Retry Command: `{_markdown_cell(repository_retry_plan.get('retry_command') or 'none')}`",
            f"- Retry Level: `{_markdown_cell(repository_retry_plan.get('retry_level') or 'none')}`",
            f"- Retry Risk: `{_markdown_cell(repository_retry_plan.get('retry_risk') or 'none')}`",
            f"- Failure Category: `{_markdown_cell(repository_retry_plan.get('failure_category') or 'none')}`",
            "",
            "### Retry Execution Result",
            "",
            f"- Present: {str(bool(repository_retry_execution_result.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_retry_execution_result.get('status'))}`",
            f"- Executed: {str(bool(repository_retry_execution_result.get('executed'))).lower()}",
            f"- Reason: `{_markdown_cell(repository_retry_execution_result.get('reason'))}`",
            f"- Retry Enabled: {str(bool(repository_retry_execution_result.get('retry_enabled'))).lower()}",
            f"- Retry Strategy: `{_markdown_cell(repository_retry_execution_result.get('retry_strategy') or 'none')}`",
            f"- Retry Command: `{_markdown_cell(repository_retry_execution_result.get('retry_command') or 'none')}`",
            f"- Return Code: {_markdown_cell(repository_retry_execution_result.get('returncode'))}",
            f"- Passed: {_markdown_cell(repository_retry_execution_result.get('passed'))}",
            f"- Failed: {_markdown_cell(repository_retry_execution_result.get('failed'))}",
            "",
            "### Dynamic Evidence",
            "",
            f"- Present: {str(bool(repository_dynamic_evidence.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_dynamic_evidence.get('status'))}`",
            f"- Reason: `{_markdown_cell(repository_dynamic_evidence.get('reason'))}`",
            f"- Evidence Level: `{_markdown_cell(repository_dynamic_evidence.get('evidence_level') or 'none')}`",
            f"- Source: `{_markdown_cell(repository_dynamic_evidence.get('source') or 'none')}`",
            f"- Failing Test Count: {_markdown_cell(repository_dynamic_evidence.get('failing_test_count'))}",
            f"- Failed Test Count: {_markdown_cell(repository_dynamic_evidence.get('failed_test_count'))}",
            f"- Passed Test Count: {_markdown_cell(repository_dynamic_evidence.get('passed_test_count'))}",
            (
                "- Usable For Localization: "
                f"{str(bool(repository_dynamic_evidence.get('usable_for_localization'))).lower()}"
            ),
            (
                "- Usable For Patch Validation: "
                f"{str(bool(repository_dynamic_evidence.get('usable_for_patch_validation'))).lower()}"
            ),
            (
                "- Usable For Regression Validation: "
                f"{str(bool(repository_dynamic_evidence.get('usable_for_regression_validation'))).lower()}"
            ),
            (
                "- Recommended Validation Command: "
                f"`{_markdown_cell(repository_dynamic_evidence.get('recommended_validation_command') or 'none')}`"
            ),
            "",
            "### Effective Execution Result",
            "",
            f"- Present: {str(bool(repository_effective_execution.get('present'))).lower()}",
            f"- Source: `{_markdown_cell(repository_effective_execution.get('source') or 'none')}`",
            f"- Status: `{_markdown_cell(repository_effective_execution.get('status') or 'none')}`",
            f"- Executed: {str(bool(repository_effective_execution.get('executed'))).lower()}",
            f"- Reason: `{_markdown_cell(repository_effective_execution.get('reason') or 'none')}`",
            f"- Command: `{_markdown_cell(repository_effective_execution.get('command') or 'none')}`",
            f"- Failure Category: `{_markdown_cell(repository_effective_execution.get('failure_category') or 'none')}`",
            f"- Failure Signal: `{_markdown_cell(repository_effective_execution.get('failure_signal') or 'none')}`",
            f"- Passed: {_markdown_cell(repository_effective_execution.get('passed'))}",
            f"- Failed: {_markdown_cell(repository_effective_execution.get('failed'))}",
            "",
            "### Analysis Route",
            "",
            f"- Analysis Source: `{_markdown_cell(repository_analysis_route.get('analysis_source') or 'none')}`",
            f"- Analysis Root: `{_markdown_cell(repository_analysis_route.get('analysis_root') or 'none')}`",
            f"- Overlay Triggered: {str(bool(repository_analysis_route.get('overlay_triggered'))).lower()}",
            f"- Overlay Trigger Reason: `{_markdown_cell(repository_analysis_route.get('overlay_trigger_reason') or 'none')}`",
            f"- Effective Evidence Level: `{_markdown_cell(repository_analysis_route.get('effective_evidence_level') or 'none')}`",
            f"- Effective Validation Command: `{_markdown_cell(repository_analysis_route.get('effective_validation_command') or 'none')}`",
            f"- Phase 2 Ready: {str(bool(repository_analysis_route.get('phase2_ready'))).lower()}",
            f"- Phase 3 Validation Ready: {str(bool(repository_analysis_route.get('phase3_validation_ready'))).lower()}",
            "",
            "### Failure Overlay",
            "",
            f"- Present: {str(bool(repository_failure_overlay.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_failure_overlay.get('status'))}`",
            f"- Reason: `{_markdown_cell(repository_failure_overlay.get('reason'))}`",
            f"- Overlay Root: `{_markdown_cell(repository_failure_overlay.get('overlay_root') or 'none')}`",
            f"- Scoped Analysis: {str(bool(repository_failure_overlay.get('analysis_scoped'))).lower()}",
            (
                "- Analysis Files: "
                f"`{_markdown_cell(_format_list(_list(repository_failure_overlay.get('analysis_files'))))}`"
            ),
            (
                "- Missing Analysis Paths: "
                f"`{_markdown_cell(_format_list(_list(repository_failure_overlay.get('missing_analysis_paths'))))}`"
            ),
            f"- Static Findings: {_markdown_cell(repository_failure_overlay.get('static_finding_count'))}",
            f"- Supported Candidates: {_markdown_cell(repository_failure_overlay.get('supported_candidate_count'))}",
            f"- Attempted Cases: {_markdown_cell(repository_failure_overlay.get('attempted_case_count'))}",
            f"- Strategy Policy: `{_markdown_cell(repository_failure_overlay.get('strategy_policy') or 'none')}`",
            (
                "- Candidate Rule Counts: "
                f"`{_markdown_cell(_format_counts(_dict(repository_failure_overlay.get('candidate_rule_counts'))))}`"
            ),
            (
                "- Attempted Rule Counts: "
                f"`{_markdown_cell(_format_counts(_dict(repository_failure_overlay.get('attempted_rule_counts'))))}`"
            ),
            (
                "- Triggered Rule Counts: "
                f"`{_markdown_cell(_format_counts(_dict(repository_failure_overlay.get('triggered_rule_counts'))))}`"
            ),
            f"- Candidate Rejections: {_markdown_cell(repository_failure_overlay.get('candidate_rejection_count'))}",
            (
                "- Candidate Rejection Counts: "
                f"`{_markdown_cell(_format_counts(_dict(repository_failure_overlay.get('candidate_rejection_counts'))))}`"
            ),
            (
                "- Candidate Rejection Examples: "
                f"`{_markdown_cell(_format_rejection_examples(_list(repository_failure_overlay.get('candidate_rejection_examples'))))}`"
            ),
            (
                "- Dominant Candidate Rejection: "
                f"`{_markdown_cell(_format_dominant_rejection(repository_failure_overlay))}`"
            ),
            (
                "- Next Overlay Extension: "
                f"`{_markdown_cell(_format_next_overlay_extension(repository_failure_overlay))}`"
            ),
            (
                "- Next Actionable Overlay Extension: "
                f"`{_markdown_cell(_format_next_actionable_overlay_extension(repository_failure_overlay))}`"
            ),
            f"- Selected Candidate Rank: {_markdown_cell(repository_failure_overlay.get('selected_candidate_rank'))}",
            f"- Selected Rule: `{_markdown_cell(repository_failure_overlay.get('selected_rule_id') or 'none')}`",
            f"- Selected Function: `{_markdown_cell(repository_failure_overlay.get('selected_function') or 'none')}`",
            (
                "- Public API Evidence: "
                f"`{_markdown_cell(_format_public_api_evidence(_dict(repository_failure_overlay.get('public_api_evidence'))))}`"
            ),
            f"- Selected Score: {_float(repository_failure_overlay.get('selected_score', 0.0)):.4f}",
            f"- Average Candidate Score: {_float(repository_failure_overlay.get('average_candidate_score', 0.0)):.4f}",
            (
                "- Selected Score Breakdown: "
                f"`{_markdown_cell(_format_score_breakdown(_dict(repository_failure_overlay.get('selected_score_breakdown'))))}`"
            ),
            (
                "- Candidate Score Preview: "
                f"`{_markdown_cell(_format_score_preview(_list(repository_failure_overlay.get('candidate_score_preview'))))}`"
            ),
            f"- Dynamic Evidence Level: `{_markdown_cell(repository_failure_overlay.get('dynamic_evidence_level') or 'none')}`",
            (
                "- Recommended Validation Command: "
                f"`{_markdown_cell(repository_failure_overlay.get('recommended_validation_command') or 'none')}`"
            ),
            "",
            "### Fault Localization",
            "",
            f"- Present: {str(bool(repository_fault_localization.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_fault_localization.get('status'))}`",
            f"- Reason: `{_markdown_cell(repository_fault_localization.get('reason'))}`",
            f"- Ranking Count: {_markdown_cell(repository_fault_localization.get('ranking_count'))}",
            f"- Top Function: `{_markdown_cell(repository_fault_localization.get('top_function') or 'none')}`",
            f"- Top Score: {_markdown_cell(repository_fault_localization.get('top_score'))}",
            f"- Matched Failed Tests: {_markdown_cell(repository_fault_localization.get('matched_failed_test_count'))}",
            f"- Unmatched Failed Tests: {_markdown_cell(repository_fault_localization.get('unmatched_failed_test_count'))}",
            (
                "- Public API Evidence: "
                f"`{_markdown_cell(_format_public_api_evidence(_dict(repository_fault_localization.get('public_api_evidence'))))}`"
            ),
            "",
            "### Patch Candidates",
            "",
            f"- Present: {str(bool(repository_patch_candidates.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_patch_candidates.get('status'))}`",
            f"- Reason: `{_markdown_cell(repository_patch_candidates.get('reason'))}`",
            f"- Candidate Count: {_markdown_cell(repository_patch_candidates.get('candidate_count'))}",
            f"- Target Function Count: {_markdown_cell(repository_patch_candidates.get('target_function_count'))}",
            (
                "- Recommended Validation Command: "
                f"`{_markdown_cell(repository_patch_candidates.get('recommended_validation_command') or 'none')}`"
            ),
            (
                "- Recommended Pytest Args: "
                f"`{_markdown_cell(' '.join(str(item) for item in _list(repository_patch_candidates.get('recommended_pytest_args'))) or 'none')}`"
            ),
            "",
            "### Patch Validation",
            "",
            f"- Present: {str(bool(repository_patch_validation.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_patch_validation.get('status'))}`",
            f"- Reason: `{_markdown_cell(repository_patch_validation.get('reason'))}`",
            f"- Candidate Count: {_markdown_cell(repository_patch_validation.get('candidate_count'))}",
            f"- Validation Limit: {_markdown_cell(repository_patch_validation.get('validation_limit'))}",
            f"- Executed Count: {_markdown_cell(repository_patch_validation.get('executed_count'))}",
            f"- Success Count: {_markdown_cell(repository_patch_validation.get('success_count'))}",
            f"- Repair Ready: {str(bool(repository_patch_validation.get('repair_ready'))).lower()}",
            f"- Repair Validation Scope: `{_markdown_cell(repository_patch_validation.get('repair_validation_scope') or 'none')}`",
            f"- Regression Ready: {str(bool(repository_patch_validation.get('regression_ready'))).lower()}",
            f"- Regression Validation: `{_markdown_cell(repository_patch_validation.get('regression_validation_status') or 'none')}` ({_markdown_cell(repository_patch_validation.get('regression_validation_reason') or 'none')})",
            f"- Regression Command: `{_markdown_cell(repository_patch_validation.get('regression_validation_command') or 'none')}`",
            f"- Requested Reflection Mode: `{_markdown_cell(repository_patch_validation.get('requested_reflection_mode') or 'none')}`",
            f"- Reflection Mode: `{_markdown_cell(repository_patch_validation.get('reflection_mode') or 'none')}`",
            f"- Reflection Refiner Status: `{_markdown_cell(repository_patch_validation.get('reflection_refiner_status') or 'none')}`",
            f"- Reflection Refiner Reason: `{_markdown_cell(repository_patch_validation.get('reflection_refiner_reason') or 'none')}`",
            f"- Reflection Candidates: {_markdown_cell(repository_patch_validation.get('reflection_candidate_count'))}",
            f"- Successful Reflection Candidates: {_markdown_cell(repository_patch_validation.get('successful_reflection_candidate_count'))}",
            f"- Max Depth Executed: {_markdown_cell(repository_patch_validation.get('max_depth_executed'))}",
            f"- Best Candidate: `{_markdown_cell(repository_patch_validation.get('best_candidate_id') or 'none')}`",
            f"- Best Candidate Success: {str(bool(repository_patch_validation.get('best_candidate_success'))).lower()}",
            f"- Best Patch File: `{_markdown_cell(repository_patch_validation.get('best_patch_relative_file_path') or 'none')}`",
            f"- Best Patch Depth: {_markdown_cell(repository_patch_validation.get('best_patch_depth'))}",
            f"- Best Patch Has Diff: {str(bool(repository_patch_validation.get('best_patch_has_diff'))).lower()}",
            "",
            "### Repair Summary",
            "",
            f"- Present: {str(bool(repository_repair_summary.get('present'))).lower()}",
            f"- Status: `{_markdown_cell(repository_repair_summary.get('status') or 'none')}`",
            f"- Reason: `{_markdown_cell(repository_repair_summary.get('reason') or 'none')}`",
            f"- Conclusion: `{_markdown_cell(repository_repair_summary.get('conclusion') or 'none')}`",
            f"- Repair Ready: {str(bool(repository_repair_summary.get('repair_ready'))).lower()}",
            f"- Validation Scope: `{_markdown_cell(repository_repair_summary.get('repair_validation_scope') or 'none')}`",
            f"- Patch Path: `{_markdown_cell(repository_repair_summary.get('patch_path') or 'none')}`",
            "",
            "| Artifact | Path |",
            "| --- | --- |",
        ]
    )
    for name, path in _dict(config.get("resolved_artifacts")).items():
        lines.append(f"| {_markdown_cell(name)} | `{_markdown_cell(path)}` |")
    return "\n".join(lines)


def _onboard_fetch_report(
    discovery: GitHubDiscoveryFetchReport,
    output_dir: str | Path,
    *,
    source: str,
    include: list[str] | None,
    exclude: list[str] | None,
    preserve_paths: bool,
    target_prefix: str,
    recipes: list[str] | None,
    source_cache_dir: str | Path | None,
    max_sources: int | None,
    max_candidates: int | None,
    auto_dependency_sources: bool,
    dependency_max_depth: int,
    preset: str,
    materialize_template: bool,
    run_benchmark: bool,
    benchmark_output_dir: str | Path | None,
    patch_mode: str,
    judge_mode: str,
    patch_judge_mode: str,
    llm_score_mode: str,
    use_dynamic_coverage: bool,
    run_quality_gate: bool,
    quality_gate_thresholds: OnboardingQualityGateThresholds | None,
    run_showcase_lite: bool,
    run_smoke_validation: bool,
    run_repository_test_command: bool,
    run_repository_test_environment_setup: bool,
    run_repository_test_retry: bool,
    run_repository_test_retry_prerequisites: bool,
    auto_repository_test_retry: bool,
    auto_repository_test_retry_max_risk: str,
    auto_repository_test_retry_allowed_runners: list[str] | None,
    repository_test_root: str | Path | None,
    repository_test_timeout: int,
    repository_test_failure_overlay_candidate_limit: int,
    repository_test_patch_validation_limit: int,
    repository_patch_generation_mode: str = "rule",
    repository_llm_patch_candidate_limit: int | None = None,
    repository_patch_candidate_variant_allowlist: list[str] | None = None,
    repository_test_reflection_mode: str,
    repository_test_reflection_rounds: int,
    repository_test_reflection_width: int,
    repository_test_environment_setup_timeout: int,
    checkout_repository_tests: bool,
    repository_checkout_timeout: int,
    repository_checkout_depth: int,
    repository_checkout_runner=None,
    repository_test_environment_setup_runner=None,
    repository_test_execution_runner=None,
    repository_test_retry_execution_runner=None,
) -> GitHubBenchmarkOnboardingReport:
    discovery_metadata = _dict(discovery.discovery_payload.get("discovery"))
    report = onboard_from_discovery(
        discovery.discovery_payload,
        output_dir,
        source=source,
        mode=discovery.mode,
        requested_urls=discovery.requested_urls,
        owner=str(discovery_metadata.get("owner") or ""),
        repo=str(discovery_metadata.get("repo") or ""),
        ref=_optional_str(discovery_metadata.get("ref")),
        include=include,
        exclude=exclude,
        preserve_paths=preserve_paths,
        target_prefix=target_prefix,
        recipes=recipes,
        source_cache_dir=source_cache_dir,
        max_sources=max_sources,
        max_candidates=max_candidates,
        auto_dependency_sources=auto_dependency_sources,
        dependency_max_depth=dependency_max_depth,
        preset=preset,
        materialize_template=materialize_template,
        run_benchmark=run_benchmark,
        benchmark_output_dir=benchmark_output_dir,
        patch_mode=patch_mode,
        judge_mode=judge_mode,
        patch_judge_mode=patch_judge_mode,
        llm_score_mode=llm_score_mode,
        use_dynamic_coverage=use_dynamic_coverage,
        run_quality_gate=run_quality_gate,
        quality_gate_thresholds=quality_gate_thresholds,
        run_showcase_lite=run_showcase_lite,
        run_smoke_validation=run_smoke_validation,
        run_repository_test_command=run_repository_test_command,
        run_repository_test_environment_setup=run_repository_test_environment_setup,
        run_repository_test_retry=run_repository_test_retry,
        run_repository_test_retry_prerequisites=run_repository_test_retry_prerequisites,
        auto_repository_test_retry=auto_repository_test_retry,
        auto_repository_test_retry_max_risk=auto_repository_test_retry_max_risk,
        auto_repository_test_retry_allowed_runners=(
            auto_repository_test_retry_allowed_runners
        ),
        repository_test_root=repository_test_root,
        repository_test_timeout=repository_test_timeout,
        repository_test_failure_overlay_candidate_limit=repository_test_failure_overlay_candidate_limit,
        repository_test_patch_validation_limit=repository_test_patch_validation_limit,
        repository_patch_generation_mode=repository_patch_generation_mode,
        repository_llm_patch_candidate_limit=repository_llm_patch_candidate_limit,
        repository_patch_candidate_variant_allowlist=repository_patch_candidate_variant_allowlist,
        repository_test_reflection_mode=repository_test_reflection_mode,
        repository_test_reflection_rounds=repository_test_reflection_rounds,
        repository_test_reflection_width=repository_test_reflection_width,
        repository_test_environment_setup_timeout=repository_test_environment_setup_timeout,
        checkout_repository_tests=checkout_repository_tests,
        repository_checkout_timeout=repository_checkout_timeout,
        repository_checkout_depth=repository_checkout_depth,
        repository_checkout_runner=repository_checkout_runner,
        repository_test_environment_setup_runner=repository_test_environment_setup_runner,
        repository_test_execution_runner=repository_test_execution_runner,
        repository_test_retry_execution_runner=repository_test_retry_execution_runner,
    )
    discovery_fetch_paths = _write_discovery_fetch_artifacts(
        Path(report.output_dir),
        replace(discovery, import_report=report.import_report),
    )
    output_paths = {**report.output_paths, **discovery_fetch_paths}
    run_config = dict(report.run_config or {})
    if run_config:
        run_config["resolved_artifacts"] = dict(output_paths)
        _write_run_config_artifacts(Path(report.output_dir), run_config)
    return replace(
        report,
        output_paths=output_paths,
        run_config=run_config or report.run_config,
    )


def _discovery_item_count(payload: dict[str, Any]) -> int:
    if isinstance(payload.get("tree"), list):
        return len(payload["tree"])
    if isinstance(payload.get("items"), list):
        return len(payload["items"])
    if isinstance(payload.get("files"), list):
        return len(payload["files"])
    if isinstance(payload.get("repositories"), list):
        return sum(
            len(repository.get("paths") or repository.get("files") or [])
            for repository in payload["repositories"]
            if isinstance(repository, dict)
        )
    return 0


def _onboarding_discovery_metadata(
    payload: dict[str, Any],
    *,
    mode: str,
    owner: str | None,
    repo: str | None,
    ref: str | None,
) -> dict[str, Any] | None:
    raw_metadata = payload.get("discovery")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    if owner:
        metadata.setdefault("owner", owner)
    if repo:
        metadata.setdefault("repo", repo)
    if ref:
        metadata.setdefault("ref", ref)
        metadata.setdefault("requested_ref", ref)
        metadata.setdefault("ref_source", "explicit")
    if mode:
        metadata.setdefault("mode", mode)
    return metadata or None


def _preserve_checkout_ref_provenance(
    checkout_payload: dict[str, Any],
    original_payload: dict[str, Any],
) -> None:
    checkout_metadata = checkout_payload.get("discovery")
    if not isinstance(checkout_metadata, dict):
        return
    original_metadata = _dict(original_payload.get("discovery"))
    for key in ("owner", "repo", "ref"):
        value = checkout_payload.get(key) or original_metadata.get(key)
        if value:
            checkout_metadata.setdefault(key, value)
    for key in (
        "cache_reuse",
        "cache_reuse_reason",
        "cache_reuse_source",
        "cache_preferred",
        "cache_preferred_source",
        "cache_fallback",
        "cache_fallback_source",
        "api_rate_limit_checkout_fallback",
        "api_rate_limit_status_code",
        "api_rate_limit_remaining",
    ):
        if key in original_metadata:
            checkout_metadata.setdefault(key, original_metadata.get(key))
    if original_metadata.get("ref_source"):
        checkout_metadata.setdefault(
            "ref_source",
            original_metadata.get("ref_source"),
        )
        checkout_metadata.setdefault(
            "requested_ref",
            original_metadata.get("requested_ref"),
        )
    elif "requested_ref" in original_metadata:
        checkout_metadata.setdefault(
            "requested_ref",
            original_metadata.get("requested_ref"),
        )


def _repository_identity_for_checkout(
    payload: dict[str, Any],
    *,
    owner: str | None,
    repo: str | None,
    ref: str | None,
) -> tuple[str, str, str | None]:
    metadata = _dict(payload.get("discovery"))
    first_file = _dict(_first(_list(payload.get("files"))))
    return (
        str(owner or metadata.get("owner") or payload.get("owner") or first_file.get("owner") or ""),
        str(repo or metadata.get("repo") or payload.get("repo") or first_file.get("repo") or ""),
        _optional_str(ref or metadata.get("ref") or payload.get("ref") or first_file.get("ref")),
    )


def _repository_checkout_skipped(
    *,
    owner: str,
    repo: str,
    ref: str | None,
    output_root: Path,
    reason: str,
    message: str,
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": reason,
        "message": message,
        "checkout_path": str(output_root / "repository_checkout"),
        "owner": owner,
        "repo": repo,
        "ref": ref or "",
        "clone_url": f"https://github.com/{owner}/{repo}.git" if owner and repo else "",
        "commands": [],
        "returncode": None,
        "timeout": False,
        "stdout_preview": "",
        "stderr_preview": "",
        "next_actions": [],
    }


def _infer_target_prefix_from_discovery(payload: dict[str, Any]) -> tuple[str, str]:
    paths = [_clean_discovery_path(path) for path in _iter_discovery_paths(payload)]
    paths = [path for path in paths if path]
    src_packages: set[str] = set()
    package_roots: set[str] = set()
    for path in paths:
        pure = PurePosixPath(path)
        if pure.name != "__init__.py":
            continue
        parts = pure.parts
        if len(parts) >= 3 and parts[0] == "src":
            src_packages.add(parts[1])
        elif len(parts) >= 2 and parts[0] not in {
            "docs",
            "examples",
            "sample",
            "samples",
            "test",
            "tests",
        }:
            package_roots.add(parts[0])
    if len(src_packages) == 1:
        return next(iter(src_packages)), "auto_src_layout"
    package_roots.discard("src")
    if len(package_roots) == 1:
        return next(iter(package_roots)), "auto_package_root"
    return "", "none"


def _iter_discovery_paths(payload: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    tree = payload.get("tree")
    if isinstance(tree, list):
        for item in tree:
            item_dict = _dict(item)
            path = str(item_dict.get("path") or "")
            if path:
                paths.append(path)
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            item_dict = _dict(item)
            path = str(item_dict.get("path") or "")
            if path:
                paths.append(path)
    files = payload.get("files")
    if isinstance(files, list):
        for item in files:
            if isinstance(item, str):
                paths.append(item)
            else:
                item_dict = _dict(item)
                path = str(item_dict.get("path") or item_dict.get("source_path") or "")
                if path:
                    paths.append(path)
    repositories = payload.get("repositories")
    if isinstance(repositories, list):
        for repository in repositories:
            repository_dict = _dict(repository)
            repo_files = repository_dict.get("paths", repository_dict.get("files", []))
            if not isinstance(repo_files, list):
                continue
            for item in repo_files:
                if isinstance(item, str):
                    paths.append(item)
                else:
                    item_dict = _dict(item)
                    path = str(item_dict.get("path") or item_dict.get("source_path") or "")
                    if path:
                        paths.append(path)
    return paths


def _clean_discovery_path(value: str) -> str:
    text = str(value).replace("\\", "/").strip().lstrip("/")
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _token_from_env(env_name: str | None) -> str | None:
    if not env_name:
        return None
    return os.environ.get(env_name)


def _validate_positive_limit(name: str, value: int | None) -> None:
    if value is not None and value <= 0:
        raise ValueError(f"{name} must be positive when provided")


def _validate_non_negative_threshold(name: str, value: int) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _validate_ratio_threshold(name: str, value: float) -> None:
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0")


def _limit_sources_payload(
    payload: dict[str, Any],
    *,
    max_sources: int | None,
    recipes: list[str] | None = None,
    source_cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    sources = _list(payload.get("sources"))
    selected = _select_source_subset(
        sources,
        max_sources,
        recipes=recipes,
        source_cache_dir=source_cache_dir,
    )
    return {"sources": selected}


def _select_source_subset(
    sources: list[Any],
    max_sources: int | None,
    *,
    recipes: list[str] | None = None,
    source_cache_dir: str | Path | None = None,
) -> list[Any]:
    if max_sources is None:
        return list(sources)

    selected: list[Any] = []
    indexed_sources = list(enumerate(sources))
    preferred_sources = [
        item for item in indexed_sources if _is_preferred_mining_source(item[1])
    ]
    remaining = preferred_sources or indexed_sources
    if max_sources >= len(remaining):
        return [source for _, source in remaining]
    seen_groups: set[str] = set()
    seen_directories: set[str] = set()
    recipe_scores = {
        index: _source_recipe_score(source, recipes=recipes, source_cache_dir=source_cache_dir)
        for index, source in remaining
    }

    while remaining and len(selected) < max_sources:
        best_position = max(
            range(len(remaining)),
            key=lambda position: (
                recipe_scores.get(remaining[position][0], 0),
                _source_diversity_gain(
                    remaining[position][1],
                    seen_groups=seen_groups,
                    seen_directories=seen_directories,
                ),
                -remaining[position][0],
            ),
        )
        _, source = remaining.pop(best_position)
        selected.append(source)
        seen_groups.add(_source_group_key(source))
        seen_directories.add(_source_directory_key(source))

    return selected


def _is_preferred_mining_source(source: Any) -> bool:
    source_dict = _dict(source)
    path_text = str(
        source_dict.get("source_path")
        or source_dict.get("target_path")
        or source_dict.get("raw_url")
        or ""
    ).replace("\\", "/")
    parts = [part.lower() for part in path_text.split("/") if part]
    if not parts:
        return False
    name = parts[-1]
    directory_parts = parts[:-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return False
    if any(part in {"tests", "test", "testing"} for part in directory_parts):
        return False
    if any(
        part in {"docs", "doc", "examples", "example", "samples", "sample"}
        for part in directory_parts
    ):
        return False
    if any(
        part in {"build", "dist", "site-packages", "migrations"}
        for part in directory_parts
    ):
        return False
    return True


def _source_limit_strategy(*, max_sources: int | None) -> str:
    if max_sources is None:
        return "all"
    return "layout_recipe_aware_diversity"


def _source_limit_summary(
    selected_sources: list[Any],
    *,
    imported_source_count: int,
    all_sources: list[Any] | None = None,
    max_sources: int | None,
    source_limit_strategy: str | None = None,
) -> dict[str, Any]:
    all_sources = list(all_sources or selected_sources)
    all_groups = {_source_group_key(source) for source in all_sources}
    selected_groups = {_source_group_key(source) for source in selected_sources}
    all_directories = {_source_directory_key(source) for source in all_sources}
    selected_directories = {
        _source_directory_key(source) for source in selected_sources
    }
    selected_keys = {_source_identity(source) for source in selected_sources}
    omitted_sources = [
        source
        for source in all_sources
        if _source_identity(source) not in selected_keys
    ]
    return {
        "source_limit": max_sources,
        "source_limit_applied": len(selected_sources) < imported_source_count,
        "source_limit_strategy": source_limit_strategy
        or ("layout_recipe_aware_diversity" if max_sources is not None else "all"),
        "selected_source_keys": [
            _source_identity(source) for source in selected_sources
        ],
        "all_source_group_count": len(all_groups),
        "selected_source_group_count": len(
            selected_groups
        ),
        "source_group_coverage": _ratio(len(selected_groups), len(all_groups)),
        "all_source_directory_count": len(all_directories),
        "selected_source_directory_count": len(
            selected_directories
        ),
        "source_directory_coverage": _ratio(
            len(selected_directories),
            len(all_directories),
        ),
        "omitted_source_count": len(omitted_sources),
        "omitted_source_group_counts": _count_source_groups(omitted_sources),
        "omitted_source_directory_counts": _count_source_directories(
            omitted_sources
        ),
    }


def _source_recipe_score(
    source: Any,
    *,
    recipes: list[str] | None,
    source_cache_dir: str | Path | None,
) -> int:
    source_dict = _dict(source)
    return _source_layout_score(source_dict) + _source_recipe_evidence_score(
        source_dict,
        recipes=recipes,
        source_cache_dir=source_cache_dir,
    )


def _source_recipe_evidence_score(
    source: dict[str, Any],
    *,
    recipes: list[str] | None,
    source_cache_dir: str | Path | None,
) -> int:
    source_dict = _dict(source)
    selected_recipes = set(recipes or [])
    if not selected_recipes:
        selected_recipes = {
            "always_true_len_check",
            "broad_exception_pass",
            "dict_missing_key_guard",
            "enumerate_start_zero_counter",
            "identity_comparison_literal",
            "inplace_api_return_value",
            "inverted_empty_guard",
            "iterator_double_consumption",
            "missing_len_zero_guard",
            "mutable_default_arg",
            "possible_index_overrun",
            "stringified_numeric_value",
        }
    path_text = (
        str(source_dict.get("source_path", ""))
        + "\n"
        + str(source_dict.get("target_path", ""))
    ).lower()
    score = _recipe_path_score(path_text, selected_recipes)
    source_text = _read_source_text_for_selection(source_dict, source_cache_dir)
    if source_text:
        score += _recipe_content_score(source_text.lower(), selected_recipes)
    return score


def _source_layout_score(source: dict[str, Any]) -> int:
    path_text = str(
        source.get("source_path")
        or source.get("target_path")
        or source.get("raw_url")
        or ""
    ).replace("\\", "/")
    parts = [part.lower() for part in path_text.split("/") if part]
    if not parts:
        return 0

    score = 0
    name = parts[-1]
    directory_parts = parts[:-1]
    if name == "__init__.py":
        score -= 20
    if directory_parts and directory_parts[0] == "src":
        score += 80
    elif len(parts) > 1 and _looks_like_package_dir(directory_parts[0]):
        score += 40
    elif len(parts) == 1:
        score += 10

    if any(part in {"tests", "test", "testing"} for part in directory_parts):
        score -= 120
    if name.startswith("test_") or name.endswith("_test.py"):
        score -= 100
    if any(
        part in {"docs", "doc", "examples", "example", "samples", "sample"}
        for part in directory_parts
    ):
        score -= 80
    if any(
        part in {"benchmarks", "benchmark", "scripts", "tools"}
        for part in directory_parts
    ):
        score -= 40
    if any(
        part in {"build", "dist", "site-packages", "migrations"}
        for part in directory_parts
    ):
        score -= 80
    return score


def _looks_like_package_dir(value: str) -> bool:
    return bool(value) and value not in {
        ".github",
        "docs",
        "examples",
        "sample",
        "samples",
        "scripts",
        "test",
        "tests",
        "tools",
    }


def _single_recipe_score(recipe: str, path_text: str, source_text: str) -> int:
    recipe_set = {recipe}
    score = _recipe_path_score(path_text, recipe_set)
    if source_text:
        score += _recipe_content_score(source_text, recipe_set)
    return score


def _recipe_path_score(path_text: str, recipes: set[str]) -> int:
    score = 0
    if "inplace_api_return_value" in recipes and any(
        token in path_text
        for token in ("format", "sort", "list", "sequence", "collection")
    ):
        score += 10
    if "missing_len_zero_guard" in recipes and any(
        token in path_text for token in ("average", "mean", "median", "ratio")
    ):
        score += 10
    if "possible_index_overrun" in recipes and any(
        token in path_text for token in ("sort", "search", "index", "array")
    ):
        score += 10
    if "dict_missing_key_guard" in recipes and any(
        token in path_text for token in ("dict", "map", "lookup", "cache")
    ):
        score += 8
    if "mutable_default_arg" in recipes and any(
        token in path_text for token in ("cache", "memo", "default")
    ):
        score += 8
    return score


def _recipe_content_score(source_text: str, recipes: set[str]) -> int:
    score = 0
    if "inplace_api_return_value" in recipes and ".sort(" in source_text:
        score += 100
    if "missing_len_zero_guard" in recipes and "len(" in source_text:
        score += 30
        if "/" in source_text or "%" in source_text or "sum(" in source_text:
            score += 50
        if "if not" in source_text or "== 0" in source_text:
            score += 20
    if "possible_index_overrun" in recipes and "range(" in source_text:
        score += 30
        if "[i +" in source_text or "[index +" in source_text:
            score += 60
    if "dict_missing_key_guard" in recipes and (
        ".get(" in source_text or "keyerror" in source_text
    ):
        score += 70
    if "mutable_default_arg" in recipes and "def " in source_text and (
        "=[]" in source_text.replace(" ", "")
        or "={}" in source_text.replace(" ", "")
    ):
        score += 70
    if "broad_exception_pass" in recipes and (
        "except exception" in source_text or "except:" in source_text
    ):
        score += 60
    if "enumerate_start_zero_counter" in recipes and "enumerate(" in source_text:
        score += 40
        if "start=1" in source_text.replace(" ", ""):
            score += 50
    if "identity_comparison_literal" in recipes and (
        " == " in source_text or " != " in source_text
    ):
        score += 25
    if "iterator_double_consumption" in recipes and "sum(" in source_text:
        score += 25
        if "enumerate(" in source_text or "iter(" in source_text:
            score += 50
    if "inverted_empty_guard" in recipes and (
        "if not" in source_text or "if len(" in source_text
    ):
        score += 35
    if "always_true_len_check" in recipes and "len(" in source_text:
        score += 35
    if "stringified_numeric_value" in recipes and any(
        token in source_text for token in (" = 0", " = 1", " = -1")
    ):
        score += 25
    return score


def _read_source_text_for_selection(
    source: dict[str, Any],
    source_cache_dir: str | Path | None,
) -> str:
    try:
        fetch_source = source_from_dict(source)
        with tempfile.TemporaryDirectory() as tmp_dir:
            written = GitHubBenchmarkFetcher().fetch_sources(
                [fetch_source],
                tmp_dir,
                cache_dir=source_cache_dir,
            )
            if not written:
                return ""
            return written[0].read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _source_diversity_gain(
    source: Any,
    *,
    seen_groups: set[str],
    seen_directories: set[str],
) -> int:
    group_key = _source_group_key(source)
    directory_key = _source_directory_key(source)
    return (
        100 * int(group_key not in seen_groups)
        + 40 * int(directory_key not in seen_directories)
    )


def _source_identity(source: Any) -> str:
    source_dict = _dict(source)
    parts = [
        str(source_dict.get("owner", "")),
        str(source_dict.get("repo", "")),
        str(source_dict.get("ref", "")),
        str(source_dict.get("source_path", "")),
        str(source_dict.get("target_path", "")),
        str(source_dict.get("raw_url", "")),
    ]
    return "|".join(parts)


def _source_group_key(source: Any) -> str:
    source_dict = _dict(source)
    owner = source_dict.get("owner")
    repo = source_dict.get("repo")
    ref = source_dict.get("ref")
    if owner or repo or ref:
        return f"{owner or ''}/{repo or ''}@{ref or ''}"
    raw_url = source_dict.get("raw_url")
    if raw_url:
        return str(raw_url)
    return _source_identity(source)


def _source_directory_key(source: Any) -> str:
    source_dict = _dict(source)
    path = str(
        source_dict.get("source_path")
        or source_dict.get("target_path")
        or source_dict.get("raw_url")
        or ""
    ).replace("\\", "/")
    parts = [part for part in path.split("/") if part]
    return parts[0] if len(parts) > 1 else "."


def _limit_mining_payload(
    payload: dict[str, Any],
    *,
    max_candidates: int | None,
) -> dict[str, Any]:
    candidates = _list(payload.get("candidates"))
    selected = _select_candidate_subset(candidates, max_candidates)
    template_cases = [
        _dict(candidate).get("template_case")
        for candidate in selected
        if isinstance(_dict(candidate).get("template_case"), dict)
    ]
    source_candidates = _source_candidates_from_template_cases(template_cases)
    rule_counts = _candidate_rule_counts(selected)
    bug_type_counts = _candidate_bug_type_counts(selected)
    all_rule_counts = _candidate_rule_counts(candidates)
    all_bug_type_counts = _candidate_bug_type_counts(candidates)
    all_candidate_sources = set().union(
        *(_candidate_source_keys(candidate) for candidate in candidates)
    ) if candidates else set()
    selected_candidate_sources = set().union(
        *(_candidate_source_keys(candidate) for candidate in selected)
    ) if selected else set()
    selected_keys = {_candidate_identity(candidate) for candidate in selected}
    omitted_candidates = [
        candidate
        for candidate in candidates
        if _candidate_identity(candidate) not in selected_keys
    ]
    quality_summary = dict(_dict(payload.get("quality_summary")))
    original_count = _int(payload.get("generated_count", len(candidates)))
    selected_count = len(selected)
    quality_summary.update(
        {
            "candidate_count": selected_count,
            "template_case_count": len(template_cases),
            "selected_candidate_count": selected_count,
            "unlimited_candidate_count": original_count,
            "candidate_limit": max_candidates,
            "candidate_limit_applied": selected_count < original_count,
            "candidate_limit_strategy": (
                "diversity_greedy" if max_candidates is not None else "all"
            ),
            "selected_candidate_ids": [
                str(_dict(candidate).get("id", "")) for candidate in selected
            ],
            "all_rule_count": len(all_rule_counts),
            "selected_rule_count": len(rule_counts),
            "candidate_rule_coverage": _ratio(len(rule_counts), len(all_rule_counts)),
            "all_bug_type_count": len(all_bug_type_counts),
            "selected_bug_type_count": len(bug_type_counts),
            "candidate_bug_type_coverage": _ratio(
                len(bug_type_counts),
                len(all_bug_type_counts),
            ),
            "all_candidate_source_count": len(all_candidate_sources),
            "selected_candidate_source_count": len(source_candidates),
            "candidate_source_coverage": _ratio(
                len(selected_candidate_sources),
                len(all_candidate_sources),
            ),
            "omitted_candidate_count": len(omitted_candidates),
            "omitted_rule_counts": _candidate_rule_counts(omitted_candidates),
            "omitted_bug_type_counts": _candidate_bug_type_counts(
                omitted_candidates
            ),
            "omitted_candidate_ids_preview": [
                _candidate_display_id(candidate)
                for candidate in omitted_candidates[:20]
            ],
        }
    )
    limited = dict(payload)
    limited.update(
        {
            "generated_count": selected_count,
            "generated_source_count": len(source_candidates),
            "rule_counts": rule_counts,
            "bug_type_counts": bug_type_counts,
            "quality_summary": quality_summary,
            "source_candidates": source_candidates,
            "candidates": selected,
            "catalog": {"candidates": selected},
            "template": {"cases": template_cases},
            "sources_payload": {"sources": source_candidates},
        }
    )
    return limited


def _select_candidate_subset(
    candidates: list[Any],
    max_candidates: int | None,
) -> list[Any]:
    if max_candidates is None or max_candidates >= len(candidates):
        return list(candidates)

    selected: list[Any] = []
    remaining = list(enumerate(candidates))
    seen_rules: set[str] = set()
    seen_bug_types: set[str] = set()
    seen_sources: set[str] = set()

    while remaining and len(selected) < max_candidates:
        best_position = max(
            range(len(remaining)),
            key=lambda position: (
                _candidate_diversity_gain(
                    remaining[position][1],
                    seen_rules=seen_rules,
                    seen_bug_types=seen_bug_types,
                    seen_sources=seen_sources,
                ),
                -remaining[position][0],
            ),
        )
        _, candidate = remaining.pop(best_position)
        selected.append(candidate)
        seen_rules.update(_candidate_rules(candidate))
        seen_bug_types.update(_candidate_bug_types(candidate))
        seen_sources.update(_candidate_source_keys(candidate))

    return selected


def _candidate_diversity_gain(
    candidate: Any,
    *,
    seen_rules: set[str],
    seen_bug_types: set[str],
    seen_sources: set[str],
) -> int:
    rules = _candidate_rules(candidate)
    bug_types = _candidate_bug_types(candidate)
    sources = _candidate_source_keys(candidate)
    return (
        100 * sum(1 for rule in rules if rule not in seen_rules)
        + 30 * sum(1 for bug_type in bug_types if bug_type not in seen_bug_types)
        + 10 * sum(1 for source in sources if source not in seen_sources)
    )


def _candidate_rules(candidate: Any) -> set[str]:
    return {str(rule_id) for rule_id in _list(_dict(candidate).get("rule_ids"))}


def _candidate_bug_types(candidate: Any) -> set[str]:
    bug_type = _dict(candidate).get("bug_type")
    return {str(bug_type)} if bug_type else set()


def _candidate_source_keys(candidate: Any) -> set[str]:
    candidate_dict = _dict(candidate)
    source_summary = _dict(candidate_dict.get("source_summary"))
    keys = {
        str(value)
        for value in (
            source_summary.get("target_path"),
            source_summary.get("source_path"),
        )
        if value
    }
    template_case = _dict(candidate_dict.get("template_case"))
    for source in _list(template_case.get("sources")):
        source_dict = _dict(source)
        for key_name in ("target_path", "source_path", "raw_url"):
            value = source_dict.get(key_name)
            if value:
                keys.add(str(value))
                break
    return keys


def _source_candidates_from_template_cases(
    template_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in template_cases:
        for source in _list(case.get("sources")):
            if not isinstance(source, dict):
                continue
            key = json.dumps(source, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            selected.append(source)
    return selected


def _candidate_rule_counts(candidates: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        for rule_id in _list(_dict(candidate).get("rule_ids")):
            rule = str(rule_id)
            counts[rule] = counts.get(rule, 0) + 1
    return dict(sorted(counts.items()))


def _candidate_bug_type_counts(candidates: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        bug_type = _dict(candidate).get("bug_type")
        if bug_type:
            key = str(bug_type)
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _count_source_groups(sources: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources:
        key = _source_group_key(source)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _count_source_directories(sources: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources:
        key = _source_directory_key(source)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _format_ratio(value: Any) -> str:
    return f"{_float(value):.3f}"


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}:{_int(value)}" for key, value in sorted(counts.items()))


def _format_public_api_evidence(evidence: dict[str, Any]) -> str:
    if not evidence:
        return "none"
    scope = str(evidence.get("trigger_scope") or "unknown")
    trigger_expression = str(evidence.get("trigger_expression") or "unknown")
    internal_target = str(evidence.get("internal_target") or "unknown")
    return f"{scope}: {trigger_expression} -> {internal_target}"


def _format_dominant_rejection(summary: dict[str, Any]) -> str:
    reason = str(summary.get("dominant_candidate_rejection_reason") or "")
    if not reason:
        return "none"
    return f"{reason}:{_int(summary.get('dominant_candidate_rejection_count', 0))}"


def _format_next_overlay_extension(summary: dict[str, Any]) -> str:
    extension = _dict(summary.get("next_overlay_extension"))
    reason = str(extension.get("reason") or "")
    recommendation = str(extension.get("recommended_extension") or "")
    if not reason and not recommendation:
        return "none"
    if not recommendation:
        return reason
    return f"{reason} -> {recommendation}"


def _format_next_actionable_overlay_extension(summary: dict[str, Any]) -> str:
    extension = _dict(summary.get("next_actionable_overlay_extension"))
    reason = str(extension.get("reason") or "")
    recommendation = str(extension.get("recommended_extension") or "")
    if not reason and not recommendation:
        return "none"
    if not recommendation:
        return reason
    return f"{reason} -> {recommendation}"


def _format_score_breakdown(breakdown: dict[str, Any]) -> str:
    if not breakdown:
        return "none"
    fields = [
        "score",
        "static_confidence",
        "rule_trigger_prior",
        "callable_kind_weight",
        "oracle_specificity",
        "assertion_oracle_bonus",
    ]
    parts = [
        f"{field}:{_float(breakdown.get(field, 0.0)):.4f}"
        for field in fields
        if field in breakdown
    ]
    return ", ".join(parts) if parts else "none"


def _format_score_preview(items: list[Any]) -> str:
    rows: list[str] = []
    for item in items[:5]:
        row = _dict(item)
        rank = _int(row.get("rank", 0))
        rule = str(row.get("rule_id") or "unknown")
        function_name = str(row.get("function_name") or "unknown")
        score = _float(row.get("overlay_score", 0.0))
        rows.append(f"{rank}:{rule}@{function_name}={score:.4f}")
    return "; ".join(rows) if rows else "none"


def _format_rejection_examples(items: list[Any]) -> str:
    rows: list[str] = []
    for item in items[:5]:
        row = _dict(item)
        rule = str(row.get("rule_id") or "unknown")
        function_name = str(
            row.get("qualified_name")
            or row.get("function_name")
            or "unknown"
        )
        reason = str(row.get("reason") or "unknown")
        rows.append(f"{rule}@{function_name}:{reason}")
    return "; ".join(rows) if rows else "none"


def _format_list(values: list[Any]) -> str:
    cleaned = [str(value) for value in values if value]
    return ", ".join(cleaned) if cleaned else "none"


def _candidate_identity(candidate: Any) -> str:
    candidate_dict = _dict(candidate)
    candidate_id = candidate_dict.get("id")
    if candidate_id:
        return f"id:{candidate_id}"
    return "json:" + json.dumps(candidate_dict, sort_keys=True, default=str)


def _candidate_display_id(candidate: Any) -> str:
    candidate_dict = _dict(candidate)
    candidate_id = candidate_dict.get("id")
    if candidate_id:
        return str(candidate_id)
    parts = [
        str(candidate_dict.get("case_name", "")),
        str(candidate_dict.get("function_name", "")),
        ",".join(sorted(_candidate_rules(candidate))),
    ]
    display = ":".join(part for part in parts if part)
    return display or _candidate_identity(candidate)


def _selected_source_entries(
    report: GitHubBenchmarkOnboardingReport,
) -> list[dict[str, Any]]:
    selected_keys = [
        str(source_key)
        for source_key in _list(report.quality_summary.get("selected_source_keys"))
        if source_key
    ]
    if not selected_keys:
        return report.import_report.source_entries[: report.selected_source_count]
    sources_by_key = {
        _source_identity(source): source
        for source in report.import_report.source_entries
        if isinstance(source, dict)
    }
    return [
        sources_by_key[source_key]
        for source_key in selected_keys
        if source_key in sources_by_key
    ]


def _selected_candidates(
    report: GitHubBenchmarkOnboardingReport,
) -> list[dict[str, Any]]:
    selected_ids = [
        str(candidate_id)
        for candidate_id in _list(report.quality_summary.get("selected_candidate_ids"))
        if candidate_id
    ]
    if not selected_ids:
        return report.mining_report.candidates[: report.generated_candidate_count]
    candidates_by_id = {
        str(candidate.get("id", "")): candidate
        for candidate in report.mining_report.candidates
        if isinstance(candidate, dict)
    }
    return [
        candidates_by_id[candidate_id]
        for candidate_id in selected_ids
        if candidate_id in candidates_by_id
    ]


def _repository_test_manifest_evidence(
    *,
    natural_evidence: dict[str, Any] | None,
    failure_overlay: dict[str, Any] | None,
    fault_localization: dict[str, Any] | None,
    execution_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    overlay = _dict(failure_overlay)
    localization = _dict(fault_localization)
    selected_case = _dict(overlay.get("selected_case"))
    overlay_dynamic_evidence = _dict(overlay.get("dynamic_evidence"))
    public_api_evidence = _dict(selected_case.get("public_api_evidence")) or _dict(
        localization.get("public_api_evidence")
    )
    overlay_case_context = _dict(
        overlay_dynamic_evidence.get("overlay_case_context")
    ) or _dict(localization.get("overlay_case_context"))
    if not public_api_evidence and not overlay_case_context:
        return {}
    return {
        "analysis_route": _repository_test_analysis_route(
            natural_evidence=_dict(natural_evidence),
            failure_overlay=overlay,
            execution_plan=_dict(execution_plan),
        ),
        "failure_overlay": {
            "status": str(overlay.get("status") or ""),
            "reason": str(overlay.get("reason") or ""),
            "selected_rule_id": str(selected_case.get("rule_id") or ""),
            "selected_function": str(selected_case.get("function_name") or ""),
            "public_api_evidence": public_api_evidence,
            "overlay_case_context": overlay_case_context,
            "recommended_validation_command": str(
                overlay.get("recommended_validation_command") or ""
            ),
        },
        "fault_localization": {
            "status": str(localization.get("status") or ""),
            "reason": str(localization.get("reason") or ""),
            "top_function": str(localization.get("top_function") or ""),
            "top_score": _float(localization.get("top_score", 0.0)),
            "public_api_evidence": _dict(
                localization.get("public_api_evidence")
            )
            or public_api_evidence,
            "overlay_case_context": _dict(localization.get("overlay_case_context"))
            or overlay_case_context,
        },
    }


def _annotate_manifest_with_repository_test_evidence(
    manifest_path: str | Path,
    evidence: dict[str, Any],
) -> None:
    if not evidence:
        return
    path = Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return
    payload["repository_test_evidence"] = evidence
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _manifest_repository_test_evidence(manifest_path: str | Path) -> dict[str, Any]:
    path = Path(manifest_path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _dict(_dict(payload).get("repository_test_evidence"))

def _benchmark_result_payload(result: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    benchmark_payload = result["benchmark_report"].to_dict()
    return {
        "output_dir": str(output_dir),
        "template_validation": result["template_validation"],
        "manifest_path": result["manifest_path"],
        "manifest_validation": result["manifest_validation"],
        "repository_test_evidence": _manifest_repository_test_evidence(
            result["manifest_path"]
        ),
        "report_artifacts": result["report_artifacts"],
        "summary": benchmark_payload.get("summary", {}),
        "cases": [
            _benchmark_case_showcase_row(case)
            for case in _list(benchmark_payload.get("cases"))
        ],
    }


def _write_showcase_lite_artifacts(
    output_dir: Path,
    showcase: dict[str, Any],
) -> dict[str, str]:
    json_path = output_dir / "onboarding_showcase_lite.json"
    markdown_path = output_dir / "onboarding_showcase_lite.md"
    json_path.write_text(
        json.dumps(showcase, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_onboarding_showcase_lite_markdown(showcase),
        encoding="utf-8",
    )
    return {
        "showcase_lite_json": str(json_path),
        "showcase_lite_markdown": str(markdown_path),
    }


def _run_onboarding_smoke_validation(
    output_dir: Path,
    report: GitHubBenchmarkOnboardingReport,
) -> tuple[OnboardingSmokeValidationReport, dict[str, str]]:
    report_json = _write_onboarding_report_json(output_dir, report)
    validation = validate_onboarding_smoke_report(report_json)
    return validation, _write_smoke_validation_artifacts(output_dir, validation)


def _write_smoke_validation_artifacts(
    output_dir: Path,
    validation: OnboardingSmokeValidationReport,
) -> dict[str, str]:
    json_path = output_dir / "onboarding_smoke_validation.json"
    markdown_path = output_dir / "onboarding_smoke_validation.md"
    json_path.write_text(
        json.dumps(validation.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_onboarding_smoke_validation_markdown(validation),
        encoding="utf-8",
    )
    return {
        "smoke_validation_json": str(json_path),
        "smoke_validation_markdown": str(markdown_path),
    }


def build_repository_config_snapshot(
    discovery_payload: dict[str, Any],
    output_dir: str | Path,
    *,
    max_files: int = 32,
    max_file_bytes: int = 200_000,
) -> dict[str, Any]:
    snapshot_root = Path(output_dir) / "repository_config_snapshot"
    rows: list[dict[str, Any]] = []
    written_files: list[str] = []
    candidates = list(
        _repository_config_snapshot_candidates(
            discovery_payload,
            max_file_bytes=max_file_bytes,
        )
    )[:max_files]
    for candidate in candidates:
        path = str(candidate.get("path") or "")
        target = _safe_snapshot_path(snapshot_root, path)
        if target is None:
            rows.append(
                {
                    "path": path,
                    "status": "skipped",
                    "reason": "unsafe_path",
                    "size": _int(candidate.get("size", 0)),
                    "url": str(candidate.get("url") or candidate.get("raw_url") or ""),
                }
            )
            continue
        text, reason = _config_snapshot_text(candidate)
        if not text:
            rows.append(
                {
                    "path": path,
                    "status": "warning",
                    "reason": reason or "empty_or_unavailable",
                    "size": _int(candidate.get("size", 0)),
                    "url": str(candidate.get("url") or candidate.get("raw_url") or ""),
                }
            )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written_files.append(path)
        rows.append(
            {
                "path": path,
                "status": "pass",
                "reason": "config_file_materialized",
                "size": len(text.encode("utf-8")),
                "url": str(candidate.get("url") or candidate.get("raw_url") or ""),
                "target_path": str(target),
            }
        )
    status = "pass" if written_files else "skipped"
    reason = "config_snapshot_built" if written_files else "no_config_files_available"
    if candidates and not written_files:
        status = "warning"
        reason = "config_snapshot_fetch_failed"
    if written_files and len(written_files) < len(candidates):
        status = "warning"
        reason = "partial_config_snapshot_built"
    return {
        "status": status,
        "reason": reason,
        "config_root": str(snapshot_root),
        "candidate_count": len(candidates),
        "file_count": len(written_files),
        "files": written_files,
        "rows": rows,
        "max_files": max_files,
        "max_file_bytes": max_file_bytes,
    }


def render_repository_config_snapshot_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repository Config Snapshot",
        "",
        f"- Status: `{_markdown_cell(payload.get('status', ''))}`",
        f"- Reason: `{_markdown_cell(payload.get('reason', ''))}`",
        f"- Config Root: `{_markdown_cell(payload.get('config_root') or 'none')}`",
        f"- Candidate Files: {_int(payload.get('candidate_count', 0))}",
        f"- Materialized Files: {_int(payload.get('file_count', 0))}",
        "",
        "| Status | Path | Reason | Size |",
        "| --- | --- | --- | ---: |",
    ]
    for row in _list(payload.get("rows")):
        item = _dict(row)
        lines.append(
            "| "
            f"{_markdown_cell(item.get('status', ''))} | "
            f"`{_markdown_cell(item.get('path', ''))}` | "
            f"{_markdown_cell(item.get('reason', ''))} | "
            f"{_int(item.get('size', 0))} |"
        )
    if not _list(payload.get("rows")):
        lines.append("| none | none | none | 0 |")
    return "\n".join(lines)


def write_repository_config_snapshot_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "repository_config_snapshot.json"
    markdown_path = root / "repository_config_snapshot.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_repository_config_snapshot_markdown(payload),
        encoding="utf-8",
    )
    return {
        "repository_config_snapshot_json": str(json_path),
        "repository_config_snapshot_markdown": str(markdown_path),
    }


def _repository_config_snapshot_candidates(
    discovery_payload: dict[str, Any],
    *,
    max_file_bytes: int,
) -> list[dict[str, Any]]:
    owner = str(discovery_payload.get("owner") or "")
    repo = str(discovery_payload.get("repo") or "")
    ref = str(discovery_payload.get("ref") or "")
    candidates: list[dict[str, Any]] = []
    for item in _discovery_file_items(discovery_payload):
        row = _dict(item)
        path = _clean_config_snapshot_path(str(row.get("path") or ""))
        if not path or not _is_repository_config_snapshot_path(path):
            continue
        size = _int(row.get("size", 0))
        if size and size > max_file_bytes:
            continue
        candidate = {
            "path": path,
            "size": size,
            "url": str(row.get("url") or ""),
            "raw_url": str(row.get("raw_url") or ""),
            "content": row.get("content"),
            "encoding": str(row.get("encoding") or ""),
            "owner": str(row.get("owner") or owner),
            "repo": str(row.get("repo") or repo),
            "ref": str(row.get("ref") or ref),
        }
        if not candidate["raw_url"] and candidate["owner"] and candidate["repo"] and candidate["ref"]:
            candidate["raw_url"] = (
                "https://raw.githubusercontent.com/"
                f"{candidate['owner']}/{candidate['repo']}/{candidate['ref']}/{path}"
            )
        candidates.append(candidate)
    return sorted(candidates, key=lambda item: str(item.get("path") or ""))


def _discovery_file_items(discovery_payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("tree", "items", "files"):
        for item in _list(discovery_payload.get(key)):
            row = _dict(item)
            if not row:
                continue
            item_type = str(row.get("type") or "blob")
            if key != "files" and item_type != "blob":
                continue
            items.append(row)
    return items


def _is_repository_config_snapshot_path(path: str) -> bool:
    name = PurePosixPath(path).name
    root_names = {
        ".python-version",
        "Pipfile",
        "Pipfile.lock",
        "hatch.toml",
        "pdm.lock",
        "pdm.toml",
        "poetry.lock",
        "pyproject.toml",
        "pytest.ini",
        "requirements-dev.txt",
        "requirements-test.txt",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
        "tox.ini",
        "uv.lock",
        "noxfile.py",
    }
    return (
        path.startswith(".github/workflows/")
        or ("/" not in path and name in root_names)
    )


def _clean_config_snapshot_path(path: str) -> str:
    normalized = str(path).replace("\\", "/").strip().strip("/")
    if not normalized or ".." in normalized.split("/"):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", normalized):
        return ""
    return normalized


def _safe_snapshot_path(root: Path, path: str) -> Path | None:
    clean = _clean_config_snapshot_path(path)
    if not clean:
        return None
    target = root / clean
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return target


def _config_snapshot_text(candidate: dict[str, Any]) -> tuple[str, str]:
    inline = candidate.get("content")
    if isinstance(inline, str) and inline:
        encoding = str(candidate.get("encoding") or "").lower()
        if encoding == "base64":
            return _decode_base64_text(inline), "inline_base64_content"
        return inline, "inline_text_content"
    url = str(candidate.get("url") or "")
    if url:
        text, reason = _fetch_github_blob_text(url)
        if text:
            return text, reason
    raw_url = str(candidate.get("raw_url") or "")
    if raw_url:
        return _fetch_raw_text(raw_url)
    return "", "missing_content_url"


def _fetch_github_blob_text(url: str) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "code-intelligence-agent",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        urllib.error.URLError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        return "", f"blob_fetch_failed:{type(exc).__name__}"
    content = str(_dict(payload).get("content") or "")
    encoding = str(_dict(payload).get("encoding") or "")
    if encoding == "base64" and content:
        return _decode_base64_text(content), "github_blob_base64"
    return "", "unsupported_blob_encoding"


def _fetch_raw_text(url: str) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "code-intelligence-agent"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="ignore"), "raw_url"
    except (OSError, urllib.error.URLError, UnicodeDecodeError) as exc:
        return "", f"raw_fetch_failed:{type(exc).__name__}"


def _decode_base64_text(content: str) -> str:
    try:
        compact = "".join(str(content).split())
        return base64.b64decode(compact).decode("utf-8", errors="ignore")
    except (ValueError, UnicodeDecodeError):
        return ""


def _write_onboarding_report_json(
    output_dir: Path,
    report: GitHubBenchmarkOnboardingReport,
) -> Path:
    report_json = output_dir / "onboarding_report.json"
    report_json.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report_json


def _write_run_config_artifacts(
    output_dir: Path,
    config: dict[str, Any],
) -> dict[str, str]:
    json_path = output_dir / "onboarding_run_config.json"
    markdown_path = output_dir / "onboarding_run_config.md"
    json_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_onboarding_run_config_markdown(config),
        encoding="utf-8",
    )
    return {
        "run_config_json": str(json_path),
        "run_config_markdown": str(markdown_path),
    }


def _write_discovery_fetch_artifacts(
    output_dir: Path,
    report: GitHubDiscoveryFetchReport,
) -> dict[str, str]:
    json_path = output_dir / "onboarding_discovery_fetch.json"
    markdown_path = output_dir / "onboarding_discovery_fetch.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_github_discovery_fetch_markdown(report),
        encoding="utf-8",
    )
    return {
        "discovery_fetch_json": str(json_path),
        "discovery_fetch_markdown": str(markdown_path),
    }


def _write_selection_audit_artifacts(
    output_dir: Path,
    audit: dict[str, Any],
) -> dict[str, str]:
    json_path = output_dir / "onboarding_selection_audit.json"
    markdown_path = output_dir / "onboarding_selection_audit.md"
    json_path.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_onboarding_selection_audit_markdown(audit),
        encoding="utf-8",
    )
    return {
        "selection_audit_json": str(json_path),
        "selection_audit_markdown": str(markdown_path),
    }


def _write_quality_gate_artifacts(
    output_dir: Path,
    result: OnboardingQualityGateResult,
) -> dict[str, str]:
    json_path = output_dir / "onboarding_quality_gate.json"
    markdown_path = output_dir / "onboarding_quality_gate.md"
    json_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_onboarding_quality_gate_markdown(result),
        encoding="utf-8",
    )
    return {
        "quality_gate_json": str(json_path),
        "quality_gate_markdown": str(markdown_path),
    }


def _write_diagnostics_artifacts(
    output_dir: Path,
    diagnostics: dict[str, Any],
) -> dict[str, str]:
    json_path = output_dir / "onboarding_diagnostics.json"
    markdown_path = output_dir / "onboarding_diagnostics.md"
    json_path.write_text(
        json.dumps(diagnostics, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_onboarding_diagnostics_markdown(diagnostics),
        encoding="utf-8",
    )
    return {
        "diagnostics_json": str(json_path),
        "diagnostics_markdown": str(markdown_path),
    }


def _write_benchmarkization_remediation_plan_artifacts(
    output_dir: Path,
    report: GitHubBenchmarkOnboardingReport,
) -> dict[str, str]:
    plan = build_benchmarkization_remediation_plan_artifact(report)
    json_path = output_dir / "benchmarkization_remediation_plan.json"
    markdown_path = output_dir / "benchmarkization_remediation_plan.md"
    json_path.write_text(
        json.dumps(plan, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_benchmarkization_remediation_plan_markdown(plan),
        encoding="utf-8",
    )
    return {
        "benchmarkization_remediation_plan_json": str(json_path),
        "benchmarkization_remediation_plan_markdown": str(markdown_path),
    }


def _diagnostic_issue(
    *,
    stage: str,
    severity: str,
    code: str,
    message: str,
    count: int,
    examples: list[dict[str, Any]],
    next_steps: list[str],
) -> dict[str, Any]:
    return {
        "stage": stage,
        "severity": severity,
        "code": code,
        "message": message,
        "count": count,
        "examples": examples,
        "next_steps": next_steps,
    }


def _diagnostic_status(issues: list[dict[str, Any]]) -> str:
    severities = {str(issue.get("severity", "")) for issue in issues}
    if "error" in severities:
        return "fail"
    if "warning" in severities:
        return "warning"
    return "pass"


def _diagnostic_next_actions(issues: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        for step in _list(issue.get("next_steps")):
            step_text = str(step)
            if step_text and step_text not in seen:
                seen.add(step_text)
                actions.append(step_text)
    return actions


def _import_skip_diagnostics(
    report: GitHubBenchmarkOnboardingReport,
) -> list[dict[str, Any]]:
    rows = []
    for row in report.import_report.rows:
        payload = row.to_dict()
        if payload.get("status") == "imported":
            continue
        source = _dict(payload.get("source"))
        rows.append(
            {
                "source_path": payload.get("source_path", ""),
                "target_path": source.get("target_path", ""),
                "reason": payload.get("reason", ""),
                "status": payload.get("status", ""),
            }
        )
    return rows


def _source_read_error_diagnostics(
    report: GitHubBenchmarkOnboardingReport,
) -> list[dict[str, Any]]:
    rows = []
    for row in report.mining_report.sources:
        row_payload = row.to_dict()
        source = _dict(row_payload.get("source"))
        for result in _list(row_payload.get("recipe_results")):
            result_dict = _dict(result)
            reasons = [str(reason) for reason in _list(result_dict.get("reasons"))]
            read_reasons = [
                reason for reason in reasons if reason.startswith("source_read_error")
            ]
            if not read_reasons:
                continue
            rows.append(
                {
                    "target_path": source.get("target_path", ""),
                    "source_path": source.get("source_path", ""),
                    "raw_url": source.get("raw_url", ""),
                    "recipe": result_dict.get("recipe", ""),
                    "status": result_dict.get("status", ""),
                    "reasons": read_reasons,
                }
            )
    return rows


def _recipe_miss_diagnostics(
    report: GitHubBenchmarkOnboardingReport,
) -> list[dict[str, Any]]:
    rows = []
    for row in report.mining_report.sources:
        row_payload = row.to_dict()
        source = _dict(row_payload.get("source"))
        for result in _list(row_payload.get("recipe_results")):
            result_dict = _dict(result)
            reasons = [str(reason) for reason in _list(result_dict.get("reasons"))]
            non_read_reasons = [
                reason
                for reason in reasons
                if not reason.startswith("source_read_error")
            ]
            if _int(result_dict.get("generated_count", 0)) > 0 or not non_read_reasons:
                continue
            rows.append(
                {
                    "target_path": source.get("target_path", ""),
                    "source_path": source.get("source_path", ""),
                    "recipe": result_dict.get("recipe", ""),
                    "status": result_dict.get("status", ""),
                    "reasons": non_read_reasons,
                }
            )
    return rows


def _recipe_suggestion_diagnostics(
    recipe_misses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for miss in recipe_misses:
        recipe = str(miss.get("recipe", "") or "unknown")
        group = grouped.setdefault(
            recipe,
            {
                "recipe": recipe,
                "miss_count": 0,
                "sources": set(),
                "reason_counts": {},
            },
        )
        group["miss_count"] += 1
        source_key = str(
            miss.get("target_path") or miss.get("source_path") or "unknown"
        )
        group["sources"].add(source_key)
        reason_counts = group["reason_counts"]
        for reason_value in _list(miss.get("reasons")):
            reason = str(reason_value)
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    suggestions = []
    for recipe, group in grouped.items():
        reason_counts = _dict(group.get("reason_counts"))
        top_reasons = [
            {"reason": reason, "count": _int(count)}
            for reason, count in sorted(
                reason_counts.items(),
                key=lambda item: (-_int(item[1]), str(item[0])),
            )[:5]
        ]
        suggestions.append(
            {
                "recipe": recipe,
                "miss_count": _int(group.get("miss_count", 0)),
                "source_count": len(group.get("sources", set())),
                "top_reasons": top_reasons,
                "suggested_actions": _recipe_suggested_actions(
                    recipe,
                    [str(row["reason"]) for row in top_reasons],
                ),
            }
        )
    return sorted(
        suggestions,
        key=lambda item: (-_int(item.get("miss_count", 0)), str(item.get("recipe", ""))),
    )


def _recipe_suggested_actions(recipe: str, reasons: list[str]) -> list[str]:
    actions = [
        "Inspect source_mining.md for the exact source x recipe miss rows.",
    ]
    for reason in reasons:
        for action in _recipe_reason_actions(recipe, reason):
            if action not in actions:
                actions.append(action)
    if len(actions) == 1:
        actions.append(
            "Broaden the run by removing --recipe or using --preset mining with more sources."
        )
    return actions[:4]


def _recipe_reason_actions(recipe: str, reason: str) -> list[str]:
    common = [
        "Increase --max-sources or adjust --include so selected files contain richer control/data-flow patterns.",
    ]
    by_reason = {
        "no_empty_guard_len_denominator_function": [
            "Target files with explicit empty-input guards followed by len-derived division.",
            "Try related guard recipes together: missing_len_zero_guard, always_true_len_check, inverted_empty_guard.",
        ],
        "no_empty_guard_with_following_main_logic": [
            "Target functions or methods where an empty-input guard is followed by a main non-empty path.",
            "Try missing_len_zero_guard or inverted_empty_guard if the source has guard logic but no separable main body.",
        ],
        "no_empty_guard_with_non_empty_oracle": [
            "Target mean/median/sort-style functions where non-empty input has a deterministic oracle.",
            "Try missing_len_zero_guard or always_true_len_check for sources with empty-input guards.",
        ],
        "no_bounded_positive_offset_index_loop": [
            "Target loops over range(len(x) - 1) or a len-derived bound that read x[i + 1].",
        ],
        "no_mapping_get_default_lookup": [
            "Target dict/mapping helpers that use .get(key, 0) or equivalent default-value lookup.",
        ],
        "no_sorted_assignment_for_inplace_api_mutation": [
            "Target sort helpers using sorted(values), typed sorted assignments, or values.sort() statements.",
        ],
        "no_numeric_assignment_for_stringified_value_mutation": [
            "Target code where len(...) or numeric expressions are assigned and later used arithmetically or as indexes.",
        ],
        "no_function_suitable_for_mutable_default_cache_mutation": [
            "Target repeatable single-argument functions or no-arg-instantiable class methods with deterministic return values.",
        ],
        "no_single_argument_function_for_broad_exception_mutation": [
            "Target single-business-argument functions or class methods that naturally raise on invalid input.",
        ],
        "no_yielding_one_based_enumerate_loop": [
            "Target generator loops using enumerate(..., start=1) or enumerate(..., 1).",
        ],
        "no_returned_literal_equality_comparison": [
            "Target classifier/parser methods that return string literal equality or inequality checks.",
        ],
        "no_materialized_iterator_average_pattern": [
            "Target average/aggregation code that materializes an iterator before sum(...) and len(...).",
        ],
    }
    return by_reason.get(reason, common)


def _quality_gate_failure_diagnostics(
    report: GitHubBenchmarkOnboardingReport,
) -> list[dict[str, Any]]:
    gate = _dict(report.quality_gate)
    rows = []
    for check in _list(gate.get("checks")):
        check_dict = _dict(check)
        if check_dict.get("passed", False):
            continue
        rows.append(
            {
                "check": check_dict.get("name", ""),
                "expected": check_dict.get("expected", ""),
                "actual": check_dict.get("actual", ""),
                "details": _list(check_dict.get("details")),
            }
        )
    return rows


def _int_check(name: str, actual: int, minimum: int) -> OnboardingQualityGateCheck:
    return OnboardingQualityGateCheck(
        name=name,
        passed=actual >= minimum,
        expected=f">= {minimum}",
        actual=str(actual),
    )


def _float_check(
    name: str,
    actual: float,
    minimum: float,
) -> OnboardingQualityGateCheck:
    return OnboardingQualityGateCheck(
        name=name,
        passed=actual >= minimum,
        expected=f">= {minimum:.4f}",
        actual=f"{actual:.4f}",
    )


def _source_showcase_row(source: dict[str, Any]) -> dict[str, Any]:
    owner = source.get("owner", "")
    repo = source.get("repo", "")
    upstream = f"{owner}/{repo}" if owner and repo else source.get("raw_url", "")
    return {
        "target_path": source.get("target_path", ""),
        "upstream": upstream,
        "ref": source.get("ref", ""),
        "source_path": source.get("source_path", ""),
        "license": source.get("license", ""),
        "sha256_present": bool(source.get("sha256")),
    }


def _source_selection_audit_row(
    source: dict[str, Any],
    *,
    recipes: list[str] | None = None,
    source_cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    row = _source_showcase_row(source)
    layout_score = _source_layout_score(source)
    recipe_score = _source_recipe_evidence_score(
        source,
        recipes=recipes,
        source_cache_dir=source_cache_dir,
    )
    row.update(
        {
            "group": _source_group_key(source),
            "directory": _source_directory_key(source),
            "identity": _source_identity(source),
            "preferred_mining_source": _is_preferred_mining_source(source),
            "layout_score": layout_score,
            "recipe_score": recipe_score,
            "total_score": layout_score + recipe_score,
        }
    )
    return row


def _candidate_showcase_row(candidate: dict[str, Any]) -> dict[str, Any]:
    template_case = _dict(candidate.get("template_case"))
    benchmark = _dict(template_case.get("benchmark"))
    metadata = _dict(benchmark.get("metadata"))
    return {
        "id": candidate.get("id", template_case.get("name", "")),
        "rule_ids": _list(candidate.get("rule_ids"))
        or _list(benchmark.get("expected_rule_ids")),
        "bug_type": candidate.get("bug_type")
        or metadata.get("bug_type", ""),
        "function_name": candidate.get("function_name")
        or _first(_list(benchmark.get("buggy_functions"))),
        "target_path": candidate.get("target_path")
        or _first(
            [
                source.get("target_path", "")
                for source in _list(template_case.get("sources"))
                if isinstance(source, dict)
            ]
        ),
        "case_name": template_case.get("name", ""),
    }


def _benchmark_case_showcase_row(case: dict[str, Any]) -> dict[str, Any]:
    ranked = _list(case.get("ranked_functions"))
    return {
        "name": case.get("name", ""),
        "top_function": _first(ranked),
        "best_patch_rule_id": case.get("best_patch_rule_id", ""),
        "patch_success": bool(
            case.get("patch_success")
            or case.get("repair_success")
            or case.get("best_patch_success")
        ),
    }


def _write_report(
    report: GitHubBenchmarkOnboardingReport,
    *,
    format_name: str,
) -> None:
    output_root = Path(report.output_dir)
    report_json = output_root / "onboarding_report.json"
    report_markdown = output_root / "onboarding_report.md"
    payload = report.to_dict()
    markdown = render_github_benchmark_onboarding_markdown(report)
    report_json.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    report_markdown.write_text(markdown, encoding="utf-8")
    if format_name == "markdown":
        print(markdown)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Onboard GitHub repository sources into benchmark-ready mutation "
            "template candidates."
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

    tree = subparsers.add_parser("tree", help="Fetch a GitHub tree and onboard it.")
    tree.add_argument("owner")
    tree.add_argument("repo")
    tree.add_argument("output_dir")
    tree.add_argument("--ref", required=True)
    tree.add_argument("--no-recursive", action="store_true")
    _add_network_args(tree)
    _add_shared_args(tree)

    repo = subparsers.add_parser(
        "repo",
        help="Fetch a GitHub repo by owner/repo or URL and onboard its tree.",
    )
    repo.add_argument("repo_spec")
    repo.add_argument("output_dir")
    repo.add_argument(
        "--ref",
        help="Commit, tag, or branch. Defaults to the repository default_branch.",
    )
    repo.add_argument("--no-recursive", action="store_true")
    _add_network_args(repo)
    _add_shared_args(repo)

    search = subparsers.add_parser(
        "search",
        help="Fetch GitHub code search results and onboard them.",
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


def _add_network_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="Environment variable containing a GitHub token.",
    )
    parser.add_argument("--api-base-url", default="https://api.github.com")
    parser.add_argument("--timeout", type=int, default=20)


def _add_shared_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preset",
        choices=["manual", "smoke", "mining"],
        default="manual",
        help=(
            "Apply a one-command onboarding preset. manual keeps explicit flags; "
            "smoke writes template, benchmark, quality gate and showcase with "
            "bounded sources/candidates; mining writes mining artifacts and a "
            "benchmark-optional quality gate."
        ),
    )
    parser.add_argument("--include", action="append")
    parser.add_argument("--exclude", action="append")
    parser.add_argument("--preserve-paths", action="store_true")
    parser.add_argument("--target-prefix", default="")
    parser.add_argument("--recipe", action="append")
    parser.add_argument("--source-cache-dir")
    parser.add_argument(
        "--max-sources",
        type=int,
        help="Limit how many imported sources enter recipe mining.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        help="Limit how many mined benchmark candidates enter catalog/template/benchmark artifacts.",
    )
    parser.add_argument(
        "--no-auto-dependency-sources",
        action="store_true",
        help=(
            "Disable the auxiliary all-Python dependency source pool used for "
            "template dependency augmentation."
        ),
    )
    parser.add_argument(
        "--dependency-max-depth",
        type=int,
        default=DEFAULT_DEPENDENCY_MAX_DEPTH,
        help=(
            "Maximum package/local import traversal depth when augmenting "
            "generated benchmark templates with selected dependency sources."
        ),
    )
    parser.add_argument("--materialize-template", action="store_true")
    parser.add_argument(
        "--run-benchmark",
        action="store_true",
        help="Run run_template_benchmark on the generated source_mining_template.",
    )
    parser.add_argument(
        "--benchmark-output-dir",
        help="Optional output directory for --run-benchmark.",
    )
    parser.add_argument(
        "--patch-mode",
        choices=["rule", "llm"],
        default="rule",
        help="Patch generation mode used by --run-benchmark.",
    )
    parser.add_argument(
        "--judge-mode",
        choices=["none", "llm"],
        default="none",
        help="Case-level judge mode used by --run-benchmark.",
    )
    parser.add_argument(
        "--patch-judge-mode",
        choices=["none", "llm"],
        default="none",
        help="Patch-level judge mode used by --run-benchmark.",
    )
    parser.add_argument(
        "--llm-score-mode",
        choices=["none", "llm"],
        default="none",
        help="Fault-localization LLMScore mode used by --run-benchmark.",
    )
    parser.add_argument(
        "--no-dynamic-coverage",
        action="store_true",
        help="Disable dynamic pytest trace coverage in --run-benchmark.",
    )
    parser.add_argument(
        "--run-quality-gate",
        action="store_true",
        help=(
            "Write a lightweight onboarding quality gate. Intended for small "
            "1-N case benchmark onboarding runs."
        ),
    )
    parser.add_argument(
        "--min-imported-sources",
        type=int,
        default=1,
        help="Minimum selected/imported sources required by --run-quality-gate.",
    )
    parser.add_argument(
        "--min-generated-candidates",
        type=int,
        default=1,
        help="Minimum generated candidates required by --run-quality-gate.",
    )
    parser.add_argument(
        "--min-quality-score",
        type=float,
        default=0.50,
        help="Minimum source mining quality score required by --run-quality-gate.",
    )
    parser.add_argument(
        "--min-source-hit-rate",
        type=float,
        default=0.50,
        help="Minimum source hit rate required by --run-quality-gate.",
    )
    parser.add_argument(
        "--min-selected-source-groups",
        type=int,
        default=1,
        help="Minimum selected upstream source groups required by --run-quality-gate.",
    )
    parser.add_argument(
        "--min-selected-source-directories",
        type=int,
        default=1,
        help="Minimum selected source directories required by --run-quality-gate.",
    )
    parser.add_argument(
        "--min-selected-rules",
        type=int,
        default=1,
        help="Minimum selected recipe/rule families required by --run-quality-gate.",
    )
    parser.add_argument(
        "--min-selected-bug-types",
        type=int,
        default=1,
        help="Minimum selected bug types required by --run-quality-gate.",
    )
    parser.add_argument(
        "--min-source-group-coverage",
        type=float,
        default=0.0,
        help=(
            "Minimum selected/all source group coverage required by "
            "--run-quality-gate."
        ),
    )
    parser.add_argument(
        "--min-source-directory-coverage",
        type=float,
        default=0.0,
        help=(
            "Minimum selected/all source directory coverage required by "
            "--run-quality-gate."
        ),
    )
    parser.add_argument(
        "--min-candidate-rule-coverage",
        type=float,
        default=0.0,
        help=(
            "Minimum selected/all candidate rule coverage required by "
            "--run-quality-gate."
        ),
    )
    parser.add_argument(
        "--min-candidate-bug-type-coverage",
        type=float,
        default=0.0,
        help=(
            "Minimum selected/all candidate bug type coverage required by "
            "--run-quality-gate."
        ),
    )
    parser.add_argument(
        "--min-candidate-source-coverage",
        type=float,
        default=0.0,
        help=(
            "Minimum selected/all candidate source coverage required by "
            "--run-quality-gate."
        ),
    )
    parser.add_argument(
        "--no-require-ready-for-benchmark",
        action="store_true",
        help="Do not require source mining ready_for_benchmark in --run-quality-gate.",
    )
    parser.add_argument(
        "--no-require-benchmark-run",
        action="store_true",
        help="Do not require --run-benchmark artifacts in --run-quality-gate.",
    )
    parser.add_argument(
        "--min-benchmark-cases",
        type=int,
        default=1,
        help="Minimum benchmark cases required by --run-quality-gate.",
    )
    parser.add_argument(
        "--min-top1",
        type=float,
        default=0.50,
        help="Minimum benchmark Top-1 required by --run-quality-gate.",
    )
    parser.add_argument(
        "--min-map",
        type=float,
        default=0.50,
        help="Minimum benchmark MAP required by --run-quality-gate.",
    )
    parser.add_argument(
        "--min-patch-success-rate",
        type=float,
        default=0.50,
        help="Minimum benchmark patch success rate required by --run-quality-gate.",
    )
    parser.add_argument(
        "--run-showcase-lite",
        action="store_true",
        help="Write onboarding_showcase_lite.json/md for small-sample review.",
    )
    parser.add_argument(
        "--run-smoke-validation",
        action="store_true",
        help=(
            "Write onboarding_smoke_validation.json/md by validating the "
            "generated onboarding report and required artifacts."
        ),
    )
    parser.add_argument(
        "--no-smoke-validation",
        action="store_true",
        help="Disable smoke validation when using --preset smoke.",
    )
    parser.add_argument(
        "--repository-test-root",
        help=(
            "Optional local full repository checkout used to execute the "
            "recommended repository test command."
        ),
    )
    parser.add_argument(
        "--repository-test-timeout",
        type=int,
        default=20,
        help="Timeout in seconds for the recommended repository test command.",
    )
    parser.add_argument(
        "--repository-test-failure-overlay-candidate-limit",
        type=int,
        default=5,
        help=(
            "Maximum generated failure-overlay candidates to attempt when "
            "repository tests do not provide localization-ready evidence."
        ),
    )
    parser.add_argument(
        "--repository-test-patch-validation-limit",
        type=int,
        default=5,
        help=(
            "Maximum patch candidates to validate in the repository-test "
            "sandbox before reflection."
        ),
    )
    parser.add_argument(
        "--repository-test-reflection-mode",
        choices=["rule", "llm", "none"],
        default="rule",
        help=(
            "Reflection refiner used by repository_test_patch_validation. "
            "The llm mode uses CIA_LLM_* environment variables."
        ),
    )
    parser.add_argument(
        "--repository-test-reflection-rounds",
        type=int,
        default=1,
        help="Maximum reflection depth for repository test patch validation.",
    )
    parser.add_argument(
        "--repository-test-reflection-width",
        type=int,
        default=1,
        help="Refined child candidates per failed parent during patch validation.",
    )
    parser.add_argument(
        "--run-repository-test-environment-setup",
        action="store_true",
        help=(
            "Create the isolated repository test venv and run supported pip "
            "install commands before planned test execution."
        ),
    )
    parser.add_argument(
        "--run-repository-test-retry",
        action="store_true",
        help=(
            "Execute the safe retry command recommended by repository_test_retry_plan "
            "when prerequisites are satisfied."
        ),
    )
    parser.add_argument(
        "--run-repository-test-retry-prerequisites",
        action="store_true",
        help=(
            "When retry requires repository test environment setup, execute the "
            "supported setup prerequisite before the retry command."
        ),
    )
    parser.add_argument(
        "--auto-repository-test-retry",
        action="store_true",
        help=(
            "Automatically execute a recommended repository test retry when "
            "its risk is within --auto-repository-test-retry-max-risk."
        ),
    )
    parser.add_argument(
        "--auto-repository-test-retry-max-risk",
        choices=["low", "medium", "high"],
        default="low",
        help="Maximum retry risk allowed for --auto-repository-test-retry.",
    )
    parser.add_argument(
        "--auto-repository-test-retry-runner",
        action="append",
        default=[],
        help=(
            "Restrict --auto-repository-test-retry to a python -m runner; "
            "repeat for multiple allowed runners such as pytest or unittest."
        ),
    )
    parser.add_argument(
        "--repository-test-environment-setup-timeout",
        type=int,
        default=120,
        help="Timeout in seconds for each repository test environment setup command.",
    )
    parser.add_argument(
        "--checkout-repository-tests",
        action="store_true",
        help=(
            "Shallow-clone the GitHub repository into repository_checkout and "
            "use it as the repository test root when possible."
        ),
    )
    parser.add_argument(
        "--repository-checkout-timeout",
        type=int,
        default=120,
        help="Timeout in seconds for git checkout commands.",
    )
    parser.add_argument(
        "--repository-checkout-depth",
        type=int,
        default=1,
        help="Depth for shallow git clone/fetch when --checkout-repository-tests is used.",
    )
    parser.add_argument(
        "--no-repository-test-command",
        action="store_true",
        help="Do not write repository_test_command validation artifacts.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
    )


def _apply_onboarding_preset(args: argparse.Namespace) -> None:
    preset = getattr(args, "preset", "manual")
    if preset == "manual":
        return
    if preset == "smoke":
        if args.max_sources is None:
            args.max_sources = 20
        if args.max_candidates is None:
            args.max_candidates = 10
        args.materialize_template = True
        args.run_benchmark = True
        args.no_dynamic_coverage = True
        args.run_quality_gate = True
        args.run_showcase_lite = True
        if not args.no_smoke_validation:
            args.run_smoke_validation = True
        return
    if preset == "mining":
        if args.max_sources is None:
            args.max_sources = 50
        if args.max_candidates is None:
            args.max_candidates = 20
        args.run_quality_gate = True
        args.run_showcase_lite = True
        args.no_require_benchmark_run = True
        return
    raise ValueError(f"Unsupported onboarding preset: {preset}")


def main(argv: list[str] | None = None, opener=None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    _apply_onboarding_preset(args)
    quality_gate_thresholds = _quality_gate_thresholds_from_args(args)

    if args.mode == "from-discovery":
        discovery_path = Path(args.discovery)
        report = onboard_from_discovery(
            json.loads(discovery_path.read_text(encoding="utf-8")),
            args.output_dir,
            source=str(discovery_path),
            owner=args.owner,
            repo=args.repo,
            ref=args.ref,
            include=args.include,
            exclude=args.exclude,
            preserve_paths=args.preserve_paths,
            target_prefix=args.target_prefix,
            recipes=args.recipe,
            source_cache_dir=args.source_cache_dir,
            max_sources=args.max_sources,
            max_candidates=args.max_candidates,
            auto_dependency_sources=not args.no_auto_dependency_sources,
            dependency_max_depth=args.dependency_max_depth,
            preset=args.preset,
            materialize_template=args.materialize_template,
            run_benchmark=args.run_benchmark,
            benchmark_output_dir=args.benchmark_output_dir,
            patch_mode=args.patch_mode,
            judge_mode=args.judge_mode,
            patch_judge_mode=args.patch_judge_mode,
            llm_score_mode=args.llm_score_mode,
            use_dynamic_coverage=not args.no_dynamic_coverage,
            run_quality_gate=args.run_quality_gate,
            quality_gate_thresholds=quality_gate_thresholds,
            run_showcase_lite=args.run_showcase_lite,
            run_smoke_validation=args.run_smoke_validation,
            run_repository_test_command=not args.no_repository_test_command,
            run_repository_test_environment_setup=args.run_repository_test_environment_setup,
            run_repository_test_retry=args.run_repository_test_retry,
            run_repository_test_retry_prerequisites=(
                args.run_repository_test_retry_prerequisites
            ),
            auto_repository_test_retry=args.auto_repository_test_retry,
            auto_repository_test_retry_max_risk=args.auto_repository_test_retry_max_risk,
            auto_repository_test_retry_allowed_runners=(
                args.auto_repository_test_retry_runner
            ),
            repository_test_root=args.repository_test_root,
            repository_test_timeout=args.repository_test_timeout,
            repository_test_failure_overlay_candidate_limit=args.repository_test_failure_overlay_candidate_limit,
            repository_test_patch_validation_limit=args.repository_test_patch_validation_limit,
            repository_test_reflection_mode=args.repository_test_reflection_mode,
            repository_test_reflection_rounds=args.repository_test_reflection_rounds,
            repository_test_reflection_width=args.repository_test_reflection_width,
            repository_test_environment_setup_timeout=args.repository_test_environment_setup_timeout,
            checkout_repository_tests=args.checkout_repository_tests,
            repository_checkout_timeout=args.repository_checkout_timeout,
            repository_checkout_depth=args.repository_checkout_depth,
        )
    elif args.mode == "repo":
        try:
            owner, repo_name, inferred_ref = parse_github_repo_spec_with_ref(
                args.repo_spec
            )
        except ValueError as exc:
            parser.error(str(exc))
        resolved_ref = args.ref or inferred_ref
        try:
            report = onboard_tree(
                owner,
                repo_name,
                resolved_ref,
                args.output_dir,
                token=_token_from_env(args.token_env),
                recursive=not args.no_recursive,
                api_base_url=args.api_base_url,
                timeout=args.timeout,
                include=args.include,
                exclude=args.exclude,
                preserve_paths=args.preserve_paths,
                target_prefix=args.target_prefix,
                recipes=args.recipe,
                source_cache_dir=args.source_cache_dir,
                max_sources=args.max_sources,
                max_candidates=args.max_candidates,
                auto_dependency_sources=not args.no_auto_dependency_sources,
                dependency_max_depth=args.dependency_max_depth,
                preset=args.preset,
                materialize_template=args.materialize_template,
                run_benchmark=args.run_benchmark,
                benchmark_output_dir=args.benchmark_output_dir,
                patch_mode=args.patch_mode,
                judge_mode=args.judge_mode,
                patch_judge_mode=args.patch_judge_mode,
                llm_score_mode=args.llm_score_mode,
                use_dynamic_coverage=not args.no_dynamic_coverage,
                run_quality_gate=args.run_quality_gate,
                quality_gate_thresholds=quality_gate_thresholds,
                run_showcase_lite=args.run_showcase_lite,
                run_smoke_validation=args.run_smoke_validation,
                run_repository_test_command=not args.no_repository_test_command,
                run_repository_test_environment_setup=args.run_repository_test_environment_setup,
                run_repository_test_retry=args.run_repository_test_retry,
                run_repository_test_retry_prerequisites=(
                    args.run_repository_test_retry_prerequisites
                ),
                auto_repository_test_retry=args.auto_repository_test_retry,
                auto_repository_test_retry_max_risk=args.auto_repository_test_retry_max_risk,
                auto_repository_test_retry_allowed_runners=(
                    args.auto_repository_test_retry_runner
                ),
                repository_test_root=args.repository_test_root,
                repository_test_timeout=args.repository_test_timeout,
                repository_test_failure_overlay_candidate_limit=args.repository_test_failure_overlay_candidate_limit,
                repository_test_patch_validation_limit=args.repository_test_patch_validation_limit,
                repository_test_reflection_mode=args.repository_test_reflection_mode,
                repository_test_reflection_rounds=args.repository_test_reflection_rounds,
                repository_test_reflection_width=args.repository_test_reflection_width,
                repository_test_environment_setup_timeout=args.repository_test_environment_setup_timeout,
                checkout_repository_tests=args.checkout_repository_tests,
                repository_checkout_timeout=args.repository_checkout_timeout,
                repository_checkout_depth=args.repository_checkout_depth,
                opener=opener,
            )
        except GitHubAPIError as exc:
            parser.exit(1, f"error: {exc}\n")
    elif args.mode == "tree":
        try:
            report = onboard_tree(
                args.owner,
                args.repo,
                args.ref,
                args.output_dir,
                token=_token_from_env(args.token_env),
                recursive=not args.no_recursive,
                api_base_url=args.api_base_url,
                timeout=args.timeout,
                include=args.include,
                exclude=args.exclude,
                preserve_paths=args.preserve_paths,
                target_prefix=args.target_prefix,
                recipes=args.recipe,
                source_cache_dir=args.source_cache_dir,
                max_sources=args.max_sources,
                max_candidates=args.max_candidates,
                auto_dependency_sources=not args.no_auto_dependency_sources,
                dependency_max_depth=args.dependency_max_depth,
                preset=args.preset,
                materialize_template=args.materialize_template,
                run_benchmark=args.run_benchmark,
                benchmark_output_dir=args.benchmark_output_dir,
                patch_mode=args.patch_mode,
                judge_mode=args.judge_mode,
                patch_judge_mode=args.patch_judge_mode,
                llm_score_mode=args.llm_score_mode,
                use_dynamic_coverage=not args.no_dynamic_coverage,
                run_quality_gate=args.run_quality_gate,
                quality_gate_thresholds=quality_gate_thresholds,
                run_showcase_lite=args.run_showcase_lite,
                run_smoke_validation=args.run_smoke_validation,
                run_repository_test_command=not args.no_repository_test_command,
                run_repository_test_environment_setup=args.run_repository_test_environment_setup,
                run_repository_test_retry=args.run_repository_test_retry,
                run_repository_test_retry_prerequisites=(
                    args.run_repository_test_retry_prerequisites
                ),
                auto_repository_test_retry=args.auto_repository_test_retry,
                auto_repository_test_retry_max_risk=args.auto_repository_test_retry_max_risk,
                auto_repository_test_retry_allowed_runners=(
                    args.auto_repository_test_retry_runner
                ),
                repository_test_root=args.repository_test_root,
                repository_test_timeout=args.repository_test_timeout,
                repository_test_failure_overlay_candidate_limit=args.repository_test_failure_overlay_candidate_limit,
                repository_test_patch_validation_limit=args.repository_test_patch_validation_limit,
                repository_test_reflection_mode=args.repository_test_reflection_mode,
                repository_test_reflection_rounds=args.repository_test_reflection_rounds,
                repository_test_reflection_width=args.repository_test_reflection_width,
                repository_test_environment_setup_timeout=args.repository_test_environment_setup_timeout,
                checkout_repository_tests=args.checkout_repository_tests,
                repository_checkout_timeout=args.repository_checkout_timeout,
                repository_checkout_depth=args.repository_checkout_depth,
                opener=opener,
            )
        except GitHubAPIError as exc:
            parser.exit(1, f"error: {exc}\n")
    else:
        try:
            report = onboard_search(
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
                include=args.include,
                exclude=args.exclude,
                preserve_paths=args.preserve_paths,
                target_prefix=args.target_prefix,
                recipes=args.recipe,
                source_cache_dir=args.source_cache_dir,
                max_sources=args.max_sources,
                max_candidates=args.max_candidates,
                auto_dependency_sources=not args.no_auto_dependency_sources,
                dependency_max_depth=args.dependency_max_depth,
                preset=args.preset,
                materialize_template=args.materialize_template,
                run_benchmark=args.run_benchmark,
                benchmark_output_dir=args.benchmark_output_dir,
                patch_mode=args.patch_mode,
                judge_mode=args.judge_mode,
                patch_judge_mode=args.patch_judge_mode,
                llm_score_mode=args.llm_score_mode,
                use_dynamic_coverage=not args.no_dynamic_coverage,
                run_quality_gate=args.run_quality_gate,
                quality_gate_thresholds=quality_gate_thresholds,
                run_showcase_lite=args.run_showcase_lite,
                run_smoke_validation=args.run_smoke_validation,
                run_repository_test_command=not args.no_repository_test_command,
                run_repository_test_environment_setup=args.run_repository_test_environment_setup,
                run_repository_test_retry=args.run_repository_test_retry,
                run_repository_test_retry_prerequisites=(
                    args.run_repository_test_retry_prerequisites
                ),
                auto_repository_test_retry=args.auto_repository_test_retry,
                auto_repository_test_retry_max_risk=args.auto_repository_test_retry_max_risk,
                auto_repository_test_retry_allowed_runners=(
                    args.auto_repository_test_retry_runner
                ),
                repository_test_root=args.repository_test_root,
                repository_test_timeout=args.repository_test_timeout,
                repository_test_failure_overlay_candidate_limit=args.repository_test_failure_overlay_candidate_limit,
                repository_test_patch_validation_limit=args.repository_test_patch_validation_limit,
                repository_test_reflection_mode=args.repository_test_reflection_mode,
                repository_test_reflection_rounds=args.repository_test_reflection_rounds,
                repository_test_reflection_width=args.repository_test_reflection_width,
                repository_test_environment_setup_timeout=args.repository_test_environment_setup_timeout,
                checkout_repository_tests=args.checkout_repository_tests,
                repository_checkout_timeout=args.repository_checkout_timeout,
                repository_checkout_depth=args.repository_checkout_depth,
                opener=opener,
            )
        except GitHubAPIError as exc:
            parser.exit(1, f"error: {exc}\n")
    _write_report(report, format_name=args.format)


def _quality_gate_thresholds_from_args(
    args: argparse.Namespace,
) -> OnboardingQualityGateThresholds:
    _validate_non_negative_threshold(
        "min_imported_sources", args.min_imported_sources
    )
    _validate_non_negative_threshold(
        "min_generated_candidates", args.min_generated_candidates
    )
    _validate_ratio_threshold("min_quality_score", args.min_quality_score)
    _validate_ratio_threshold("min_source_hit_rate", args.min_source_hit_rate)
    _validate_non_negative_threshold(
        "min_selected_source_groups", args.min_selected_source_groups
    )
    _validate_non_negative_threshold(
        "min_selected_source_directories", args.min_selected_source_directories
    )
    _validate_non_negative_threshold("min_selected_rules", args.min_selected_rules)
    _validate_non_negative_threshold(
        "min_selected_bug_types", args.min_selected_bug_types
    )
    _validate_ratio_threshold(
        "min_source_group_coverage", args.min_source_group_coverage
    )
    _validate_ratio_threshold(
        "min_source_directory_coverage", args.min_source_directory_coverage
    )
    _validate_ratio_threshold(
        "min_candidate_rule_coverage", args.min_candidate_rule_coverage
    )
    _validate_ratio_threshold(
        "min_candidate_bug_type_coverage", args.min_candidate_bug_type_coverage
    )
    _validate_ratio_threshold(
        "min_candidate_source_coverage", args.min_candidate_source_coverage
    )
    _validate_non_negative_threshold("min_benchmark_cases", args.min_benchmark_cases)
    _validate_ratio_threshold("min_top1", args.min_top1)
    _validate_ratio_threshold("min_map", args.min_map)
    _validate_ratio_threshold(
        "min_patch_success_rate", args.min_patch_success_rate
    )
    return OnboardingQualityGateThresholds(
        min_imported_sources=args.min_imported_sources,
        min_generated_candidates=args.min_generated_candidates,
        min_quality_score=args.min_quality_score,
        min_source_hit_rate=args.min_source_hit_rate,
        min_selected_source_groups=args.min_selected_source_groups,
        min_selected_source_directories=args.min_selected_source_directories,
        min_selected_rules=args.min_selected_rules,
        min_selected_bug_types=args.min_selected_bug_types,
        min_source_group_coverage=args.min_source_group_coverage,
        min_source_directory_coverage=args.min_source_directory_coverage,
        min_candidate_rule_coverage=args.min_candidate_rule_coverage,
        min_candidate_bug_type_coverage=args.min_candidate_bug_type_coverage,
        min_candidate_source_coverage=args.min_candidate_source_coverage,
        require_ready_for_benchmark=not args.no_require_ready_for_benchmark,
        require_benchmark_run=not args.no_require_benchmark_run,
        min_benchmark_cases=args.min_benchmark_cases,
        min_top1=args.min_top1,
        min_map=args.min_map,
        min_patch_success_rate=args.min_patch_success_rate,
    )


def _markdown_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _repository_test_analysis_route(
    *,
    natural_evidence: dict[str, Any],
    failure_overlay: dict[str, Any],
    execution_plan: dict[str, Any],
) -> dict[str, Any]:
    natural = _dict(natural_evidence)
    overlay = _dict(failure_overlay)
    overlay_dynamic = _dict(overlay.get("dynamic_evidence"))
    natural_usable = bool(natural.get("usable_for_localization", False))
    overlay_usable = bool(
        str(overlay.get("status") or "") == "pass"
        and overlay_dynamic.get("usable_for_localization", False)
    )
    overlay_triggered = bool(overlay)
    if natural_usable:
        source = "natural_dynamic_evidence"
        evidence = natural
        analysis_root = str(execution_plan.get("repository_root") or "")
    elif overlay_usable:
        source = "failure_overlay_dynamic_evidence"
        evidence = overlay_dynamic
        analysis_root = str(overlay.get("overlay_root") or "")
    else:
        source = "none"
        evidence = overlay_dynamic if overlay_dynamic else natural
        analysis_root = str(
            overlay.get("overlay_root") or execution_plan.get("repository_root") or ""
        )
    return {
        "analysis_source": source,
        "analysis_root": analysis_root,
        "overlay_triggered": overlay_triggered,
        "overlay_trigger_reason": _failure_overlay_trigger_reason(
            natural,
            overlay,
            overlay_triggered=overlay_triggered,
        ),
        "natural_evidence_level": str(natural.get("evidence_level") or ""),
        "natural_evidence_source": str(natural.get("source") or ""),
        "natural_usable_for_localization": natural_usable,
        "overlay_status": str(overlay.get("status") or ""),
        "overlay_reason": str(overlay.get("reason") or ""),
        "overlay_evidence_level": str(overlay_dynamic.get("evidence_level") or ""),
        "overlay_usable_for_localization": overlay_usable,
        "effective_evidence_level": str(evidence.get("evidence_level") or ""),
        "effective_validation_command": str(
            evidence.get("recommended_validation_command") or ""
        ),
        "phase2_ready": source in {
            "natural_dynamic_evidence",
            "failure_overlay_dynamic_evidence",
        },
        "phase3_validation_ready": bool(
            evidence.get("usable_for_patch_validation", False)
        ),
    }


def _retry_plan_requires_environment_setup(
    retry_plan: dict[str, Any] | None,
) -> bool:
    plan = _dict(retry_plan)
    return bool(
        plan.get("retry_recommended", False)
        and str(plan.get("retry_strategy") or "")
        == "run_environment_setup_then_retry"
    )


def _should_auto_run_repository_test_retry(
    retry_plan: dict[str, Any] | None,
    repository_test_environment_setup_result: dict[str, Any] | None,
    *,
    enabled: bool,
    max_risk: str,
    allowed_runners: list[str] | None = None,
) -> bool:
    if not enabled:
        return False
    plan = _dict(retry_plan)
    if not bool(plan.get("retry_recommended", False)):
        return False
    retry_command = str(plan.get("retry_command") or "").strip()
    if not retry_command:
        return False
    if not _retry_risk_allowed(str(plan.get("retry_risk") or ""), max_risk):
        return False
    if not _retry_runner_allowed(
        _python_module_runner(retry_command),
        allowed_runners,
    ):
        return False
    if _retry_plan_requires_environment_setup(plan):
        setup_result = _dict(repository_test_environment_setup_result)
        return str(setup_result.get("status") or "") == "pass"
    return True


def _should_attempt_pytest_plugin_repair(
    retry_execution_result: dict[str, Any] | None,
    repository_test_environment_setup_result: dict[str, Any] | None,
) -> bool:
    retry = _dict(retry_execution_result)
    setup_result = _dict(repository_test_environment_setup_result)
    return bool(
        retry.get("executed", False)
        and str(retry.get("failure_category") or "") == "missing_pytest_fixture"
        and str(setup_result.get("status") or "") == "pass"
    )


def _should_attempt_timeout_narrowing(
    retry_execution_result: dict[str, Any] | None,
) -> bool:
    retry = _dict(retry_execution_result)
    return bool(
        retry.get("executed", False)
        and str(retry.get("failure_category") or "") == "timeout"
    )


def _python_module_runner(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return ""
    if len(parts) >= 3 and parts[1] == "-m":
        return str(parts[2])
    return ""


def _repository_test_regression_validation_command(
    dynamic_evidence: dict[str, Any] | None,
    execution_plan: dict[str, Any] | None,
) -> str:
    evidence = _dict(dynamic_evidence)
    plan = _dict(execution_plan)
    selected = _dict(evidence.get("selected_execution"))
    commands: list[str] = []
    if bool(evidence.get("usable_for_regression_validation", False)):
        commands.extend(
            [
                str(selected.get("command") or ""),
                str(evidence.get("primary_validation_command") or ""),
                str(evidence.get("recommended_validation_command") or ""),
            ]
        )
    commands.extend(
        [
            str(plan.get("recommended_execution_command") or ""),
            str(evidence.get("primary_validation_command") or ""),
        ]
    )
    seen: set[str] = set()
    for command in commands:
        command = str(command or "").strip()
        if not command or command in seen:
            continue
        seen.add(command)
        if _pytest_args_from_python_module_command(command):
            return command
    return ""


def _pytest_args_from_python_module_command(command: str) -> list[str]:
    try:
        args = shlex.split(command)
    except ValueError:
        return []
    if len(args) < 3:
        return []
    executable = Path(args[0]).name.lower()
    if executable not in {
        "python",
        "python.exe",
        "python3",
        "python3.exe",
        "py",
        "py.exe",
    }:
        return []
    if args[1] != "-m" or args[2] != "pytest":
        return []
    return [arg for arg in args[3:] if arg not in {"-q", "--quiet"}]


def _retry_runner_allowed(
    runner: str,
    allowed_runners: list[str] | None,
) -> bool:
    allowed = {str(item).strip() for item in (allowed_runners or []) if str(item).strip()}
    if not allowed:
        return True
    return str(runner or "").strip() in allowed


def _retry_risk_allowed(risk: str, max_risk: str) -> bool:
    order = {"low": 0, "medium": 1, "high": 2}
    actual = order.get(str(risk or "").lower())
    allowed = order.get(str(max_risk or "").lower())
    if actual is None or allowed is None:
        return False
    return actual <= allowed


def _setup_result_can_run_as_retry_prerequisite(
    setup_result: dict[str, Any] | None,
) -> bool:
    result = _dict(setup_result)
    if str(result.get("status") or "") == "pass":
        return False
    if bool(result.get("executed", False)):
        return False
    return str(result.get("reason") or "") in {
        "",
        "execution_disabled",
        "setup_plan_not_ready",
        "missing_venv_create_command",
        "no_install_command",
        "repository_root_missing",
    }


def _failure_overlay_trigger_reason(
    natural_evidence: dict[str, Any],
    failure_overlay: dict[str, Any],
    *,
    overlay_triggered: bool,
) -> str:
    if not overlay_triggered:
        if bool(natural_evidence.get("usable_for_localization", False)):
            return "natural_evidence_localization_ready"
        return "overlay_not_attempted"
    level = str(natural_evidence.get("evidence_level") or "")
    if level == "passing_tests":
        return "natural_tests_passing"
    if level == "not_executed":
        return "natural_tests_not_executed"
    if level in {"environment_failure", "collection_failure", "timeout", "unknown_failure"}:
        return f"natural_tests_{level}"
    if not natural_evidence:
        return "no_natural_dynamic_evidence"
    if bool(natural_evidence.get("usable_for_localization", False)):
        return "natural_evidence_localization_ready"
    return str(failure_overlay.get("reason") or "natural_evidence_not_localization_ready")


def _repository_test_failure_overlay_analysis_paths(
    sources_payload: dict[str, Any],
    *,
    repository_root: str | Path | None,
) -> list[str]:
    if repository_root is None:
        return []

    root = Path(repository_root)
    paths: list[str] = []
    seen: set[str] = set()
    for source in _list(_dict(sources_payload).get("sources")):
        source_dict = _dict(source)
        candidates = [
            str(source_dict.get(key) or "").strip()
            for key in ("source_path", "target_path", "raw_url")
            if str(source_dict.get(key) or "").strip()
            and not _looks_like_remote_url(str(source_dict.get(key) or "").strip())
        ]
        existing_candidates = [
            value
            for value in candidates
            if _overlay_analysis_path_exists_under_root(
                value,
                repository_root=root,
            )
        ]
        for value in existing_candidates or candidates:
            _append_unique_overlay_analysis_path(
                paths,
                seen,
                value,
                repository_root=root,
            )
    return paths


def _append_unique_overlay_analysis_path(
    paths: list[str],
    seen: set[str],
    value: str,
    *,
    repository_root: Path,
) -> None:
    normalized = value.replace("\\", "/")
    path = Path(value)
    if path.is_absolute() and not _path_is_under_root(path, repository_root):
        return
    candidate = path if path.is_absolute() else repository_root / normalized
    try:
        key = candidate.resolve().as_posix()
    except (OSError, RuntimeError):
        key = normalized
    if key in seen:
        return
    seen.add(key)
    paths.append(value)


def _overlay_analysis_path_exists_under_root(
    value: str,
    *,
    repository_root: Path,
) -> bool:
    path = Path(value)
    candidate = path if path.is_absolute() else repository_root / value.replace("\\", "/")
    if path.is_absolute() and not _path_is_under_root(path, repository_root):
        return False
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return False
    return (
        _path_is_under_root(resolved, repository_root)
        and resolved.exists()
        and (resolved.is_file() or resolved.is_dir())
    )


def _path_is_under_root(path: Path, root: Path) -> bool:
    try:
        resolved_path = path.resolve()
        resolved_root = root.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _looks_like_remote_url(value: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value))


def _planned_repository_test_python(
    environment_setup: dict[str, Any] | None,
    environment_setup_result: dict[str, Any] | None,
) -> tuple[str | None, str]:
    setup = _dict(environment_setup)
    setup_result = _dict(environment_setup_result)
    venv_python = str(setup.get("venv_python") or "").strip()
    if setup_result.get("status") == "pass" and venv_python:
        return venv_python, "repository_test_environment_setup"
    return None, "current_interpreter"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first(values: list[Any]) -> Any:
    return values[0] if values else ""


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    main()
