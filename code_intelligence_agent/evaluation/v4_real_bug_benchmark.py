from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from code_intelligence_agent.evaluation.v3_experiment_protocol import (
    canonical_json_sha256,
    sha256_file,
)
from code_intelligence_agent.evaluation.v3_real_bug_benchmark import (
    inspect_setup_script,
    parse_assignment_file,
    parse_patch_ground_truth,
)


SCHEMA_VERSION = "4.0"
INVENTORY_SCHEMA_VERSION = "4.0"
CASE_STATUSES = {"candidate", "accepted", "rejected"}
SPLITS = {"development", "validation", "test"}
EXPECTED_ACCEPTED_SPLITS = {"development": 10, "validation": 15, "test": 25}
REQUIRED_DIFFICULTY_CATEGORIES = {
    "static_negative",
    "cross_function",
    "dataflow",
    "multi_file",
    "root_error_separated",
    "high_similarity_candidates",
    "real_traceback",
}
LEGACY_DIFFICULTY_NAMES = {
    "data_flow": "dataflow",
    "separated_failure_site": "root_error_separated",
}
DIRECT_TEST_RUNNERS = {"pytest", "unittest", "nose"}
ADAPTER_TEST_RUNNERS = {"tox", "nox"}
NATIVE_RISK_REQUIREMENTS = {
    "blis",
    "cffi",
    "cymem",
    "h5py",
    "lxml",
    "matplotlib",
    "murmurhash",
    "numpy",
    "pandas",
    "preshed",
    "scipy",
    "spacy",
    "tensorflow",
    "thinc",
}
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHORT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SHELL_CONTROL_PATTERN = re.compile(r"(?:&&|\|\||[|;<>`])")
REQUIREMENT_NAME_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)")


def load_json_object(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def build_bugsinpy_inventory(
    source_root: str | Path,
    *,
    source_commit: str,
    available_python_versions: set[str] | None = None,
) -> dict[str, Any]:
    root = Path(source_root).resolve()
    projects_root = root / "projects"
    if not projects_root.is_dir():
        raise ValueError("BugsInPy projects directory is missing.")
    if not COMMIT_PATTERN.fullmatch(source_commit):
        raise ValueError("source_commit must be a full 40-character SHA.")
    available = set(available_python_versions or set())
    cases: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for project_root in sorted(projects_root.iterdir(), key=lambda item: item.name.lower()):
        if not project_root.is_dir():
            continue
        try:
            project_info = parse_assignment_file(project_root / "project.info")
        except (OSError, ValueError) as exc:
            errors.append(
                {"project": project_root.name, "bug_id": "", "reason": str(exc)}
            )
            continue
        bugs_root = project_root / "bugs"
        for case_root in sorted(
            (item for item in bugs_root.iterdir() if item.is_dir()),
            key=lambda item: _int(item.name, 0),
        ):
            try:
                cases.append(
                    _inventory_bugsinpy_case(
                        root=root,
                        project_root=project_root,
                        project_info=project_info,
                        case_root=case_root,
                        available_python_versions=available,
                    )
                )
            except (OSError, ValueError) as exc:
                errors.append(
                    {
                        "project": project_root.name,
                        "bug_id": case_root.name,
                        "reason": str(exc),
                    }
                )
    inventory = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "inventory_id": "v4-bugsinpy-offline-candidate-inventory",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "benchmark": "BugsInPy",
            "repository_url": "https://github.com/soarsmu/BugsInPy",
            "commit_sha": source_commit,
            "source_root_committed": False,
            "repository_scripts_executed": False,
        },
        "available_python_versions": sorted(available),
        "case_count": len(cases),
        "inventory_error_count": len(errors),
        "inventory_errors": errors,
        "cases": cases,
    }
    inventory["inventory_sha256"] = inventory_fingerprint(inventory)
    return inventory


def _inventory_bugsinpy_case(
    *,
    root: Path,
    project_root: Path,
    project_info: dict[str, str],
    case_root: Path,
    available_python_versions: set[str],
) -> dict[str, Any]:
    bug_id = case_root.name
    if not bug_id.isdigit() or _int(bug_id, 0) <= 0:
        raise ValueError(f"Invalid bug id: {bug_id}")
    bug_info = parse_assignment_file(case_root / "bug.info")
    repository_url = str(project_info.get("github_url") or "").rstrip("/")
    if not repository_url.startswith("https://github.com/"):
        raise ValueError("Project github_url is missing or unsupported.")
    bug_sha = str(bug_info.get("buggy_commit_id") or "").lower()
    fix_sha = str(bug_info.get("fixed_commit_id") or "").lower()
    if not SHORT_COMMIT_PATTERN.fullmatch(bug_sha) or not SHORT_COMMIT_PATTERN.fullmatch(fix_sha):
        raise ValueError("Bug and fix revisions must be hexadecimal Git revisions.")
    python_version = str(bug_info.get("python_version") or "")
    test_profile = classify_test_script(case_root / "run_test.sh")
    setup_observation = inspect_setup_script(case_root / "setup.sh")
    ground_truth = parse_patch_ground_truth(case_root / "bug_patch.txt")
    requirements = profile_requirements(case_root / "requirements.txt")
    runtime_available = python_version in available_python_versions
    eligibility = _candidate_eligibility(
        test_profile=test_profile,
        setup_observation=setup_observation,
        ground_truth=ground_truth,
        requirements=requirements,
        runtime_available=runtime_available,
        commit_resolution_required=(
            not COMMIT_PATTERN.fullmatch(bug_sha)
            or not COMMIT_PATTERN.fullmatch(fix_sha)
        ),
    )
    project_name = project_root.name
    metadata_path = (
        Path("projects") / project_name / "bugs" / bug_id / "bug.info"
    ).as_posix()
    patch_path = (
        Path("projects") / project_name / "bugs" / bug_id / "bug_patch.txt"
    ).as_posix()
    test_paths = [
        item.strip()
        for item in str(bug_info.get("test_file") or "").split(";")
        if item.strip()
    ]
    return {
        "case_id": f"bugsinpy-{project_name.lower()}-{bug_id}",
        "project": project_name,
        "bug_id": _int(bug_id, 0),
        "repository": {
            "url": repository_url,
            "owner_repo": repository_url.removeprefix("https://github.com/"),
        },
        "bug_commit_sha": bug_sha,
        "fix_commit_sha": fix_sha,
        "commit_resolution": {
            "bug_commit_is_full_sha": bool(COMMIT_PATTERN.fullmatch(bug_sha)),
            "fix_commit_is_full_sha": bool(COMMIT_PATTERN.fullmatch(fix_sha)),
            "required": (
                not COMMIT_PATTERN.fullmatch(bug_sha)
                or not COMMIT_PATTERN.fullmatch(fix_sha)
            ),
        },
        "python_version": python_version,
        "runtime_available": runtime_available,
        "declared_test_paths": test_paths,
        "targeted_test": test_profile,
        "setup_observation": setup_observation,
        "requirements": requirements,
        "ground_truth_summary": {
            **ground_truth,
            "benchmark_patch_path": patch_path,
            "visible_to_model": False,
        },
        "provenance": {
            "benchmark": "BugsInPy",
            "benchmark_case": f"{project_name}:{bug_id}",
            "benchmark_metadata_path": metadata_path,
            "bug_commit_url": f"{repository_url}/commit/{bug_sha}",
            "fix_commit_url": f"{repository_url}/commit/{fix_sha}",
        },
        "eligibility": eligibility,
    }


def classify_test_script(path: str | Path) -> dict[str, Any]:
    commands: list[list[str]] = []
    runners: list[str] = []
    errors: list[str] = []
    raw_lines: list[str] = []
    for line_number, raw_line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        raw_lines.append(line)
        if SHELL_CONTROL_PATTERN.search(line):
            errors.append(f"line_{line_number}:shell_control_forbidden")
            continue
        try:
            parts = shlex.split(line, posix=True)
        except ValueError:
            errors.append(f"line_{line_number}:invalid_shell_quoting")
            continue
        normalized, runner, error = _normalize_test_command(parts)
        if error:
            errors.append(f"line_{line_number}:{error}")
            continue
        commands.append(normalized)
        runners.append(runner)
    runner_set = sorted(set(runners))
    if not raw_lines:
        errors.append("no_test_command")
    if len(runner_set) > 1:
        errors.append("multiple_test_runners_require_manual_review")
    runner = runner_set[0] if len(runner_set) == 1 else ""
    if errors:
        adapter_status = "unsupported"
    elif runner in DIRECT_TEST_RUNNERS:
        adapter_status = "ready"
    elif runner in ADAPTER_TEST_RUNNERS:
        adapter_status = "adapter_required"
    else:
        adapter_status = "unsupported"
    return {
        "raw_lines": raw_lines,
        "normalized_commands": commands,
        "runner": runner,
        "adapter_status": adapter_status,
        "safe_argv_only": not errors,
        "errors": errors,
    }


def _normalize_test_command(parts: list[str]) -> tuple[list[str], str, str]:
    if not parts:
        return [], "", "empty_test_command"
    executable = Path(parts[0]).name.lower()
    if executable in {"pytest", "pytest.exe", "py.test", "py.test.exe"}:
        return ["{python}", "-m", "pytest", *parts[1:]], "pytest", ""
    if executable in {"tox", "tox.exe", "nox", "nox.exe"}:
        runner = executable.removesuffix(".exe")
        return ["{python}", "-m", runner, *parts[1:]], runner, ""
    if executable in {"python", "python3", "python.exe"}:
        if len(parts) < 3 or parts[1] != "-m":
            return [], "", "python_test_command_requires_-m"
        runner = parts[2].lower()
        if runner == "py.test":
            runner = "pytest"
        if runner not in DIRECT_TEST_RUNNERS | ADAPTER_TEST_RUNNERS:
            return [], "", f"unsupported_python_module:{runner}"
        return ["{python}", "-m", runner, *parts[3:]], runner, ""
    return [], "", f"unsupported_test_executable:{executable}"


def profile_requirements(path: str | Path) -> dict[str, Any]:
    requirement_path = Path(path)
    lines = _read_metadata_text(requirement_path).splitlines() if requirement_path.is_file() else []
    entries = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    names: list[str] = []
    vcs_or_editable = False
    for entry in entries:
        lowered = entry.lower()
        if lowered.startswith(("-e ", "git+", "hg+", "svn+", "bzr+")):
            vcs_or_editable = True
            continue
        match = REQUIREMENT_NAME_PATTERN.match(entry)
        if match:
            names.append(match.group(1).lower().replace("_", "-"))
    native_hits = sorted(set(names) & NATIVE_RISK_REQUIREMENTS)
    return {
        "path": requirement_path.name if requirement_path.is_file() else "",
        "sha256": sha256_file(requirement_path) if requirement_path.is_file() else "",
        "entry_count": len(entries),
        "contains_vcs_or_editable_requirement": vcs_or_editable,
        "native_build_risk_packages": native_hits,
        "native_build_risk": bool(native_hits),
    }


def _candidate_eligibility(
    *,
    test_profile: dict[str, Any],
    setup_observation: dict[str, Any],
    ground_truth: dict[str, Any],
    requirements: dict[str, Any],
    runtime_available: bool,
    commit_resolution_required: bool,
) -> dict[str, Any]:
    blocking_reasons: list[str] = []
    review_reasons: list[str] = []
    adapter_status = str(test_profile.get("adapter_status") or "")
    if adapter_status == "unsupported":
        blocking_reasons.append("unsupported_targeted_test_command")
    if str(setup_observation.get("risk_level") or "") == "high":
        review_reasons.append("high_risk_setup_requires_safe_replacement")
    if _int(ground_truth.get("source_file_count"), 0) < 1:
        blocking_reasons.append("gold_patch_has_no_source_file")
    if commit_resolution_required:
        blocking_reasons.append("short_commit_sha_requires_resolution")
    requires_adapter = (
        adapter_status == "adapter_required" or bool(review_reasons)
    )
    status = "blocked" if blocking_reasons else (
        "needs_adapter" if requires_adapter else "eligible"
    )
    score = 0
    score += 35 if adapter_status == "ready" else 10 if adapter_status == "adapter_required" else -40
    score += 20 if runtime_available else 0
    score += {"none": 15, "low": 12, "medium": 2, "high": -30}.get(
        str(setup_observation.get("risk_level") or ""),
        -10,
    )
    source_count = _int(ground_truth.get("source_file_count"), 0)
    score += 12 if source_count == 1 else 8 if source_count == 2 else -5
    score += 5 if _int(requirements.get("entry_count"), 0) <= 50 else 0
    score -= 12 if requirements.get("native_build_risk") is True else 0
    return {
        "status": status,
        "score": score,
        "blocking_reasons": blocking_reasons,
        "review_reasons": review_reasons,
        "requires_runtime_provisioning": not runtime_available,
        "requires_test_adapter": adapter_status == "adapter_required",
        "requires_safe_setup_replacement": bool(review_reasons),
        "manual_reproduction_required": True,
    }


def inventory_fingerprint(inventory: dict[str, Any]) -> str:
    value = copy.deepcopy(inventory)
    value.pop("generated_at", None)
    value.pop("inventory_sha256", None)
    return canonical_json_sha256(value)


def build_v4_seed_catalog(
    *,
    v3_catalog: dict[str, Any],
    inventory: dict[str, Any],
    selection_plan: dict[str, Any],
) -> dict[str, Any]:
    errors = validate_selection_plan(
        selection_plan,
        inventory=inventory,
        v3_catalog=v3_catalog,
    )
    if errors:
        raise ValueError("Invalid selection plan: " + ";".join(errors))
    cases = [_migrate_v3_case(_dict(item)) for item in _list(v3_catalog.get("cases"))]
    inventory_by_key = {
        (str(item.get("project") or ""), _int(item.get("bug_id"), 0)): _dict(item)
        for item in _list(inventory.get("cases"))
    }
    for project in [_dict(item) for item in _list(selection_plan.get("projects"))]:
        project_name = str(project.get("name") or "")
        for bug_id in [_int(item, 0) for item in _list(project.get("candidate_bug_ids"))]:
            inventory_case = inventory_by_key[(project_name, bug_id)]
            cases.append(_candidate_from_inventory(inventory_case, project))
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "catalog_id": str(selection_plan.get("catalog_id") or "v4-real-python-bugs-seed"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "seed_unlocked",
        "locked": False,
        "sources": [
            {
                "benchmark": "V3 frozen real-bug catalog",
                "catalog_id": str(v3_catalog.get("catalog_id") or ""),
                "catalog_sha256": str(v3_catalog.get("catalog_sha256") or ""),
            },
            {
                "benchmark": "BugsInPy",
                "inventory_sha256": str(inventory.get("inventory_sha256") or ""),
                "repository_url": str(_dict(inventory.get("source")).get("repository_url") or ""),
                "commit_sha": str(_dict(inventory.get("source")).get("commit_sha") or ""),
            },
        ],
        "target": {
            "accepted_case_count": 50,
            "minimum_repository_count": 15,
            "accepted_split_counts": dict(EXPECTED_ACCEPTED_SPLITS),
            "split_policy": "repository_disjoint",
        },
        "selection_plan": {
            "plan_id": str(selection_plan.get("plan_id") or ""),
            "plan_sha256": canonical_json_sha256(selection_plan),
        },
        "cases": sorted(cases, key=lambda item: str(item.get("case_id") or "")),
    }
    catalog["manifest_sha256"] = catalog_fingerprint(catalog)
    catalog["summary"] = summarize_catalog(catalog)
    return catalog


def validate_selection_plan(
    plan: dict[str, Any],
    *,
    inventory: dict[str, Any],
    v3_catalog: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if str(plan.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append("selection_plan.schema_version_must_be_4.0")
    if str(plan.get("inventory_sha256") or "") != str(
        inventory.get("inventory_sha256") or ""
    ):
        errors.append("selection_plan.inventory_sha256_mismatch")
    inventory_by_key = {
        (str(item.get("project") or ""), _int(item.get("bug_id"), 0)): _dict(item)
        for item in _list(inventory.get("cases"))
    }
    seen_projects: set[str] = set()
    seen_cases: set[tuple[str, int]] = set()
    planned_target_counts = {split: 0 for split in SPLITS}
    planned_repositories: dict[str, str] = {}
    for index, project in enumerate(_list(plan.get("projects"))):
        item = _dict(project)
        name = str(item.get("name") or "")
        if not name or name in seen_projects:
            errors.append(f"selection_plan.invalid_or_duplicate_project:{index}")
        seen_projects.add(name)
        split = str(item.get("benchmark_split") or "")
        if split not in SPLITS:
            errors.append(f"selection_plan.invalid_split:{name}")
        target_count = _int(item.get("target_accepted_count"), 0)
        if target_count <= 0:
            errors.append(f"selection_plan.target_accepted_count_must_be_positive:{name}")
        elif split in planned_target_counts:
            planned_target_counts[split] += target_count
        license_config = _dict(item.get("license"))
        if not str(license_config.get("spdx") or ""):
            errors.append(f"selection_plan.license_spdx_is_required:{name}")
        if not str(license_config.get("path") or ""):
            errors.append(f"selection_plan.license_path_is_required:{name}")
        license_url_template = str(license_config.get("url_template") or "")
        if not license_url_template.startswith("https://"):
            errors.append(f"selection_plan.license_url_template_is_required:{name}")
        elif "{bug_commit_sha}" not in license_url_template:
            errors.append(f"selection_plan.license_url_template_requires_bug_sha:{name}")
        regression = [str(value) for value in _list(item.get("regression_command"))]
        if len(regression) < 3 or regression[:2] != ["{python}", "-m"]:
            errors.append(f"selection_plan.regression_command_is_invalid:{name}")
        candidate_ids = [_int(value, 0) for value in _list(item.get("candidate_bug_ids"))]
        if len(candidate_ids) < target_count:
            errors.append(f"selection_plan.insufficient_candidate_backfill:{name}")
        project_candidates: dict[int, dict[str, Any]] = {}
        for bug_id in candidate_ids:
            key = (name, bug_id)
            if key in seen_cases:
                errors.append(f"selection_plan.duplicate_case:{name}:{bug_id}")
            seen_cases.add(key)
            candidate = inventory_by_key.get(key)
            if candidate is None:
                errors.append(f"selection_plan.case_not_in_inventory:{name}:{bug_id}")
            elif str(_dict(candidate.get("eligibility")).get("status") or "") == "blocked":
                errors.append(f"selection_plan.blocked_case_selected:{name}:{bug_id}")
            else:
                project_candidates[bug_id] = candidate

        owner_repo = str(item.get("owner_repo") or "")
        inventory_repositories = {
            str(_dict(candidate.get("repository")).get("owner_repo") or "")
            for candidate in project_candidates.values()
        }
        if not owner_repo:
            errors.append(f"selection_plan.owner_repo_is_required:{name}")
        elif inventory_repositories and inventory_repositories != {owner_repo}:
            errors.append(f"selection_plan.owner_repo_mismatch:{name}")
        elif split in SPLITS:
            planned_repositories[owner_repo] = split

        verification = _dict(license_config.get("verification"))
        if str(verification.get("status") or "") != "verified_at_representative_bug_commit":
            errors.append(f"selection_plan.license_verification_is_required:{name}")
        if str(verification.get("method") or "") not in {
            "github_rest_license_endpoint",
            "github_contents_api",
        }:
            errors.append(f"selection_plan.license_verification_method_is_invalid:{name}")
        evidence_url = str(verification.get("evidence_url") or "")
        if not evidence_url.startswith("https://"):
            errors.append(f"selection_plan.license_evidence_url_is_required:{name}")
        representative_id = _int(verification.get("representative_bug_id"), 0)
        representative = project_candidates.get(representative_id)
        if representative is None:
            errors.append(f"selection_plan.license_representative_case_is_invalid:{name}")
        else:
            expected_commit = str(representative.get("bug_commit_sha") or "")
            verified_commit = str(verification.get("commit_sha") or "")
            if verified_commit != expected_commit or not COMMIT_PATTERN.fullmatch(
                verified_commit
            ):
                errors.append(f"selection_plan.license_commit_mismatch:{name}")
            if verified_commit and verified_commit not in evidence_url:
                errors.append(f"selection_plan.license_evidence_url_commit_mismatch:{name}")

    target_contract = _dict(plan.get("target_contract"))
    if target_contract.get("enforce") is True:
        if v3_catalog is None:
            errors.append("selection_plan.target_contract_requires_v3_catalog")
        else:
            baseline_counts = _accepted_split_counts(v3_catalog)
            expected_additions = {
                split: EXPECTED_ACCEPTED_SPLITS[split] - baseline_counts[split]
                for split in SPLITS
            }
            declared_baseline = {
                split: _int(_dict(target_contract.get("baseline_accepted_split_counts")).get(split), -1)
                for split in SPLITS
            }
            declared_additions = {
                split: _int(_dict(target_contract.get("planned_accepted_additions")).get(split), -1)
                for split in SPLITS
            }
            if declared_baseline != baseline_counts:
                errors.append("selection_plan.target_contract_baseline_mismatch")
            if declared_additions != expected_additions:
                errors.append("selection_plan.target_contract_additions_mismatch")
            if planned_target_counts != expected_additions:
                errors.append("selection_plan.project_targets_do_not_fill_split_deficits")

            existing_repository_splits: dict[str, set[str]] = {}
            accepted_repositories: set[str] = set()
            for case in [_dict(value) for value in _list(v3_catalog.get("cases"))]:
                repository = str(
                    _dict(case.get("repository")).get("owner_repo") or ""
                )
                case_split = str(case.get("benchmark_split") or "")
                if repository:
                    existing_repository_splits.setdefault(repository, set()).add(
                        case_split
                    )
                    if case.get("status") == "accepted":
                        accepted_repositories.add(repository)
            for repository, split in planned_repositories.items():
                if repository in existing_repository_splits and existing_repository_splits[
                    repository
                ] != {split}:
                    errors.append(
                        f"selection_plan.repository_split_leakage:{repository}:{split}"
                    )
            if len(accepted_repositories | set(planned_repositories)) < 15:
                errors.append(
                    "selection_plan.cannot_reach_minimum_accepted_repository_count"
                )
    return errors


def _accepted_split_counts(catalog: dict[str, Any]) -> dict[str, int]:
    cases = [_dict(item) for item in _list(catalog.get("cases"))]
    return {
        split: sum(
            item.get("status") == "accepted"
            and item.get("benchmark_split") == split
            for item in cases
        )
        for split in SPLITS
    }


def _migrate_v3_case(case: dict[str, Any]) -> dict[str, Any]:
    repository = _dict(case.get("repository"))
    provenance = _dict(case.get("provenance"))
    ground_truth = _dict(case.get("ground_truth"))
    reproduction = _dict(case.get("reproduction"))
    difficulty_evidence = {
        LEGACY_DIFFICULTY_NAMES.get(str(key), str(key)): str(value)
        for key, value in _dict(case.get("difficulty_tag_evidence")).items()
    }
    difficulties = [
        LEGACY_DIFFICULTY_NAMES.get(str(item), str(item))
        for item in _list(case.get("difficulty_tags"))
    ]
    if case.get("status") == "accepted" and "real_traceback" not in difficulties:
        difficulties.append("real_traceback")
        difficulty_evidence["real_traceback"] = (
            "The frozen V3 reproduction contains a real targeted failing test and "
            "captured execution evidence."
        )
    source_url = str(provenance.get("issue_or_pr_url") or "") or str(
        provenance.get("fix_commit_url") or ""
    )
    return {
        "case_id": str(case.get("case_id") or ""),
        "status": str(case.get("status") or "candidate"),
        "benchmark_split": str(case.get("benchmark_split") or ""),
        "repository": {
            "url": str(repository.get("url") or ""),
            "owner_repo": str(repository.get("owner_repo") or ""),
            "license_spdx": str(repository.get("license_spdx") or ""),
            "license_url": str(repository.get("license_url") or ""),
        },
        "source_url": source_url,
        "bug_commit_sha": str(case.get("bug_commit_sha") or ""),
        "fix_commit_sha": str(case.get("fix_commit_sha") or ""),
        "targeted_tests": copy.deepcopy(_list(case.get("targeted_test_commands"))),
        "regression_tests": [copy.deepcopy(_list(case.get("regression_command")))],
        "ground_truth": {
            "patch_sha256": str(ground_truth.get("patch_sha256") or ""),
            "source_files": copy.deepcopy(_list(ground_truth.get("source_files"))),
            "test_files": copy.deepcopy(_list(ground_truth.get("test_files"))),
            "functions": copy.deepcopy(_list(ground_truth.get("functions"))),
            "visible_to_model": False,
            "source": "frozen_v3_catalog",
        },
        "difficulty_categories": sorted(set(difficulties)),
        "difficulty_evidence": difficulty_evidence,
        "difficulty_review_status": "verified" if case.get("status") == "accepted" else "not_applicable",
        "environment": {
            "python_version": str(case.get("python_version") or ""),
            "environment_profile_id": str(case.get("environment_profile_id") or ""),
            "setup_observation": copy.deepcopy(_dict(case.get("setup_observation"))),
            "test_overlay_paths": copy.deepcopy(_list(case.get("test_overlay_paths"))),
        },
        "reproduction": {
            **copy.deepcopy(reproduction),
            "source": "frozen_v3_reproduction",
            "verification_artifact": "docs/v3/phase1_verification.json",
        },
        "provenance": copy.deepcopy(provenance),
        "model_context_audit": {
            "contains_gold_patch": False,
            "contains_fix_commit_content": False,
            "contains_hidden_test_answer": False,
        },
        "rejection_reason": str(case.get("rejection_reason") or ""),
        "rejection_evidence": copy.deepcopy(_dict(case.get("rejection_evidence"))),
    }


def _candidate_from_inventory(
    inventory_case: dict[str, Any],
    project_plan: dict[str, Any],
) -> dict[str, Any]:
    repository = _dict(inventory_case.get("repository"))
    license_config = _dict(project_plan.get("license"))
    bug_sha = str(inventory_case.get("bug_commit_sha") or "")
    ground_truth = _dict(inventory_case.get("ground_truth_summary"))
    inferred_difficulties: list[str] = []
    inferred_evidence: dict[str, str] = {}
    if _int(ground_truth.get("source_file_count"), 0) > 1:
        inferred_difficulties.append("multi_file")
        inferred_evidence["multi_file"] = "Gold metadata changes more than one source file; final classification remains pending reproduction review."
    if len(_list(ground_truth.get("functions"))) > 1:
        inferred_difficulties.append("cross_function")
        inferred_evidence["cross_function"] = "Gold metadata touches multiple named symbols; final causal classification remains pending reproduction review."
    targeted = _dict(inventory_case.get("targeted_test"))
    return {
        "case_id": str(inventory_case.get("case_id") or ""),
        "status": "candidate",
        "benchmark_split": str(project_plan.get("benchmark_split") or ""),
        "repository": {
            "url": str(repository.get("url") or ""),
            "owner_repo": str(repository.get("owner_repo") or ""),
            "license_spdx": str(license_config.get("spdx") or ""),
            "license_url": str(license_config.get("url_template") or "").format(
                bug_commit_sha=bug_sha
            ),
        },
        "source_url": str(_dict(inventory_case.get("provenance")).get("fix_commit_url") or ""),
        "bug_commit_sha": bug_sha,
        "fix_commit_sha": str(inventory_case.get("fix_commit_sha") or ""),
        "targeted_tests": copy.deepcopy(_list(targeted.get("normalized_commands"))),
        "regression_tests": [copy.deepcopy(_list(project_plan.get("regression_command")))],
        "ground_truth": {
            "patch_sha256": str(ground_truth.get("patch_sha256") or ""),
            "source_files": copy.deepcopy(_list(ground_truth.get("source_files"))),
            "test_files": copy.deepcopy(_list(ground_truth.get("test_files"))),
            "functions": copy.deepcopy(_list(ground_truth.get("functions"))),
            "visible_to_model": False,
            "source": "bugsinpy_offline_metadata",
        },
        "difficulty_categories": inferred_difficulties,
        "difficulty_evidence": inferred_evidence,
        "difficulty_review_status": "pending_manual_review",
        "environment": {
            "python_version": str(inventory_case.get("python_version") or ""),
            "environment_profile_id": "",
            "runtime_available": inventory_case.get("runtime_available") is True,
            "test_runner": str(targeted.get("runner") or ""),
            "test_adapter_status": str(targeted.get("adapter_status") or ""),
            "declared_test_paths": copy.deepcopy(_list(inventory_case.get("declared_test_paths"))),
            "setup_observation": copy.deepcopy(_dict(inventory_case.get("setup_observation"))),
            "requirements": copy.deepcopy(_dict(inventory_case.get("requirements"))),
        },
        "reproduction": {
            "status": "pending",
            "bug_targeted": {"status": "pending"},
            "fix_targeted": {"status": "pending"},
            "fix_full_regression": {"status": "pending"},
            "acceptance": {"reproducible": False},
            "evidence_artifact": "",
        },
        "provenance": copy.deepcopy(_dict(inventory_case.get("provenance"))),
        "model_context_audit": {
            "contains_gold_patch": False,
            "contains_fix_commit_content": False,
            "contains_hidden_test_answer": False,
        },
        "selection": {
            "inventory_eligibility": copy.deepcopy(_dict(inventory_case.get("eligibility"))),
            "project_target_accepted_count": _int(project_plan.get("target_accepted_count"), 0),
        },
        "rejection_reason": "",
        "rejection_evidence": {},
    }


def validate_v4_catalog(
    catalog: dict[str, Any],
    *,
    require_locked: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if str(catalog.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append("schema_version_must_be_4.0")
    cases = [_dict(item) for item in _list(catalog.get("cases"))]
    case_ids: set[str] = set()
    repository_splits: dict[str, set[str]] = {}
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        case_id = str(case.get("case_id") or "")
        if not case_id:
            errors.append(f"{prefix}.case_id_is_required")
        elif case_id in case_ids:
            errors.append(f"duplicate_case_id:{case_id}")
        case_ids.add(case_id)
        status = str(case.get("status") or "")
        if status not in CASE_STATUSES:
            errors.append(f"{prefix}.status_is_invalid")
        split = str(case.get("benchmark_split") or "")
        if split not in SPLITS:
            errors.append(f"{prefix}.benchmark_split_is_invalid")
        repository = _dict(case.get("repository"))
        owner_repo = str(repository.get("owner_repo") or "")
        if not owner_repo:
            errors.append(f"{prefix}.repository.owner_repo_is_required")
        repository_splits.setdefault(owner_repo, set()).add(split)
        for field in ("bug_commit_sha", "fix_commit_sha"):
            if not COMMIT_PATTERN.fullmatch(str(case.get(field) or "")):
                errors.append(f"{prefix}.{field}_must_be_full_sha")
        if not str(case.get("source_url") or "").startswith("https://"):
            errors.append(f"{prefix}.source_url_is_required")
        if not str(repository.get("license_spdx") or ""):
            errors.append(f"{prefix}.repository.license_spdx_is_required")
        if not str(repository.get("license_url") or "").startswith("https://"):
            errors.append(f"{prefix}.repository.license_url_is_required")
        if not _commands_are_safe(_list(case.get("targeted_tests"))):
            errors.append(f"{prefix}.targeted_tests_are_invalid")
        if not _commands_are_safe(_list(case.get("regression_tests"))):
            errors.append(f"{prefix}.regression_tests_are_invalid")
        ground_truth = _dict(case.get("ground_truth"))
        if not SHA256_PATTERN.fullmatch(str(ground_truth.get("patch_sha256") or "")):
            errors.append(f"{prefix}.ground_truth.patch_sha256_is_invalid")
        if not _list(ground_truth.get("source_files")):
            errors.append(f"{prefix}.ground_truth.source_files_are_required")
        if ground_truth.get("visible_to_model") is not False:
            errors.append(f"{prefix}.ground_truth_must_be_hidden_from_model")
        context = _dict(case.get("model_context_audit"))
        for field in (
            "contains_gold_patch",
            "contains_fix_commit_content",
            "contains_hidden_test_answer",
        ):
            if context.get(field) is not False:
                errors.append(f"{prefix}.model_context_audit.{field}_must_be_false")
        difficulties = set(map(str, _list(case.get("difficulty_categories"))))
        evidence = _dict(case.get("difficulty_evidence"))
        if status == "accepted":
            for difficulty in difficulties:
                if not str(evidence.get(difficulty) or ""):
                    errors.append(f"{prefix}.difficulty_evidence_missing:{difficulty}")
            _validate_accepted_reproduction(case, prefix, errors)
        if status == "candidate" and str(_dict(case.get("reproduction")).get("status") or "") != "pending":
            errors.append(f"{prefix}.candidate_reproduction_must_be_pending")
        if status == "rejected":
            if not str(case.get("rejection_reason") or ""):
                errors.append(f"{prefix}.rejected_case_requires_reason")
            if not str(_dict(case.get("rejection_evidence")).get("summary") or ""):
                errors.append(f"{prefix}.rejected_case_requires_evidence_summary")
    for owner_repo, splits in repository_splits.items():
        if len(splits) != 1:
            errors.append(f"repository_split_leakage:{owner_repo}:{','.join(sorted(splits))}")

    summary = summarize_catalog(catalog)
    expected_hash = str(catalog.get("manifest_sha256") or "")
    actual_hash = catalog_fingerprint(catalog)
    if expected_hash != actual_hash:
        errors.append("manifest_sha256_mismatch")
    locked = catalog.get("locked") is True
    if require_locked or locked:
        if not locked:
            errors.append("catalog_must_be_locked")
        if _int(summary.get("accepted_case_count"), 0) != 50:
            errors.append("locked_catalog_requires_exactly_50_accepted_cases")
        if _int(summary.get("accepted_repository_count"), 0) < 15:
            errors.append("locked_catalog_requires_at_least_15_repositories")
        accepted_splits = _dict(summary.get("accepted_split_counts"))
        if accepted_splits != EXPECTED_ACCEPTED_SPLITS:
            errors.append("locked_catalog_requires_10_15_25_split")
        covered = set(map(str, _list(summary.get("accepted_difficulty_categories"))))
        missing = sorted(REQUIRED_DIFFICULTY_CATEGORIES - covered)
        if missing:
            errors.append("locked_catalog_missing_difficulty_categories:" + ",".join(missing))
        if _int(summary.get("candidate_case_count"), 0) != 0:
            errors.append("locked_catalog_cannot_contain_candidates")
    elif _int(summary.get("accepted_case_count"), 0) < 20:
        warnings.append("seed_catalog_has_fewer_than_20_accepted_cases")
    return {
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
        "manifest_sha256": actual_hash,
    }


def _validate_accepted_reproduction(
    case: dict[str, Any],
    prefix: str,
    errors: list[str],
) -> None:
    reproduction = _dict(case.get("reproduction"))
    if str(_dict(reproduction.get("bug_targeted")).get("status") or "") != "fail":
        errors.append(f"{prefix}.accepted_case_requires_bug_target_failure")
    if str(_dict(reproduction.get("fix_targeted")).get("status") or "") != "pass":
        errors.append(f"{prefix}.accepted_case_requires_fix_target_pass")
    if str(_dict(reproduction.get("fix_full_regression")).get("status") or "") != "pass":
        errors.append(f"{prefix}.accepted_case_requires_fix_regression_pass")
    acceptance = _dict(reproduction.get("acceptance"))
    if acceptance.get("reproducible") is not True:
        errors.append(f"{prefix}.accepted_case_requires_reproducible_evidence")


def summarize_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    cases = [_dict(item) for item in _list(catalog.get("cases"))]
    accepted = [item for item in cases if item.get("status") == "accepted"]
    candidates = [item for item in cases if item.get("status") == "candidate"]
    rejected = [item for item in cases if item.get("status") == "rejected"]
    accepted_repositories = {
        str(_dict(item.get("repository")).get("owner_repo") or "") for item in accepted
    }
    all_repositories = {
        str(_dict(item.get("repository")).get("owner_repo") or "") for item in cases
    }
    accepted_difficulties = sorted(
        {
            str(category)
            for item in accepted
            for category in _list(item.get("difficulty_categories"))
        }
    )
    return {
        "case_count": len(cases),
        "accepted_case_count": len(accepted),
        "candidate_case_count": len(candidates),
        "rejected_case_count": len(rejected),
        "repository_count": len(all_repositories - {""}),
        "accepted_repository_count": len(accepted_repositories - {""}),
        "accepted_split_counts": {
            split: sum(item.get("benchmark_split") == split for item in accepted)
            for split in ("development", "validation", "test")
        },
        "candidate_split_counts": {
            split: sum(item.get("benchmark_split") == split for item in candidates)
            for split in ("development", "validation", "test")
        },
        "accepted_difficulty_categories": accepted_difficulties,
    }


def catalog_fingerprint(catalog: dict[str, Any]) -> str:
    value = copy.deepcopy(catalog)
    value.pop("generated_at", None)
    value.pop("manifest_sha256", None)
    value.pop("summary", None)
    return canonical_json_sha256(value)


def _commands_are_safe(value: list[Any]) -> bool:
    if not value:
        return False
    for command in value:
        parts = [str(item) for item in _list(command)]
        if len(parts) < 3 or parts[:2] != ["{python}", "-m"]:
            return False
        if any(not item or SHELL_CONTROL_PATTERN.search(item) for item in parts):
            return False
    return True


def _read_metadata_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def render_catalog_markdown(catalog: dict[str, Any], audit: dict[str, Any]) -> str:
    summary = _dict(audit.get("summary"))
    lines = [
        "# V4 Real-Bug Seed Catalog",
        "",
        f"- Status: `{audit.get('status')}`",
        f"- Locked: `{catalog.get('locked')}`",
        f"- Manifest SHA-256: `{audit.get('manifest_sha256')}`",
        f"- Cases: `{summary.get('case_count')}`",
        f"- Accepted/candidate/rejected: `{summary.get('accepted_case_count')}` / `{summary.get('candidate_case_count')}` / `{summary.get('rejected_case_count')}`",
        f"- Accepted repositories: `{summary.get('accepted_repository_count')}`",
        "",
        "| Split | Accepted | Candidate | Target |",
        "| --- | ---: | ---: | ---: |",
    ]
    accepted_splits = _dict(summary.get("accepted_split_counts"))
    candidate_splits = _dict(summary.get("candidate_split_counts"))
    for split in ("development", "validation", "test"):
        lines.append(
            f"| {split} | {accepted_splits.get(split, 0)} | "
            f"{candidate_splits.get(split, 0)} | {EXPECTED_ACCEPTED_SPLITS[split]} |"
        )
    lines.extend(
        [
            "",
            "This seed catalog is not a final benchmark lock. Candidate cases must be independently reproduced, manually classified, and either accepted or moved to the rejected catalog with evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def write_catalog_artifacts(
    catalog: dict[str, Any],
    output_prefix: str | Path,
) -> dict[str, str]:
    audit = validate_v4_catalog(catalog)
    prefix = Path(output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    catalog_path = prefix.with_suffix(".json")
    audit_path = prefix.with_name(prefix.name + "_audit").with_suffix(".json")
    markdown_path = prefix.with_suffix(".md")
    _write_lf(catalog_path, json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    _write_lf(audit_path, json.dumps(audit, indent=2, ensure_ascii=False) + "\n")
    _write_lf(markdown_path, render_catalog_markdown(catalog, audit))
    return {
        "catalog_json": str(catalog_path),
        "audit_json": str(audit_path),
        "catalog_markdown": str(markdown_path),
    }


def _write_lf(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory, seed, and audit the V4 real-Python-bug benchmark."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("source_root")
    inventory.add_argument("output")
    inventory.add_argument("--source-commit", required=True)
    inventory.add_argument("--available-python", action="append", default=[])

    seed = subparsers.add_parser("seed")
    seed.add_argument("inventory")
    seed.add_argument("selection_plan")
    seed.add_argument("v3_catalog")
    seed.add_argument("output_prefix")

    audit = subparsers.add_parser("audit")
    audit.add_argument("catalog")
    audit.add_argument("--require-locked", action="store_true")
    audit.add_argument("--format", choices=["json", "markdown"], default="markdown")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    if args.command == "inventory":
        inventory = build_bugsinpy_inventory(
            args.source_root,
            source_commit=args.source_commit,
            available_python_versions=set(args.available_python),
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        _write_lf(output, json.dumps(inventory, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps({"status": "pass", "case_count": inventory["case_count"], "inventory_sha256": inventory["inventory_sha256"]}, indent=2))
        return
    if args.command == "seed":
        catalog = build_v4_seed_catalog(
            v3_catalog=load_json_object(args.v3_catalog),
            inventory=load_json_object(args.inventory),
            selection_plan=load_json_object(args.selection_plan),
        )
        paths = write_catalog_artifacts(catalog, args.output_prefix)
        print(json.dumps(paths, indent=2))
        return
    catalog = load_json_object(args.catalog)
    result = validate_v4_catalog(catalog, require_locked=args.require_locked)
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_catalog_markdown(catalog, result))
    if result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
