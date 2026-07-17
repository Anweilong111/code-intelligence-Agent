from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from code_intelligence_agent.evaluation.v3_experiment_protocol import (
    canonical_json_sha256,
)
from code_intelligence_agent.evaluation.github_repository_checkout import (
    checkout_github_repository,
)
from code_intelligence_agent.evaluation.v3_real_bug_reproduction import (
    audit_python_runtime,
    prepare_real_bug_case,
    reproduce_real_bug_case,
)
from code_intelligence_agent.evaluation.v4_real_bug_benchmark import (
    load_json_object,
    validate_selection_plan,
    validate_v4_catalog,
)
from code_intelligence_agent.tools.runtime_security import build_restricted_environment


SCHEMA_VERSION = "4.0"
SUPPORTED_EXECUTION_MODULES = {"nose", "pytest", "unittest"}
ADAPTABLE_MODULES = {"nox", "py.test", "tox"}
SAFE_MODULE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
SAFE_PYTHON_MODULE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_RUNTIME_SUPPORT_ROOT = ".cia-runtime-support"
RuntimeProbe = Callable[[Path, str, list[str]], dict[str, Any]]
SUPPORTED_EXECUTION_PLATFORMS = {"any", "darwin", "linux", "windows"}


def validate_reproduction_profiles(profiles: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if str(profiles.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append("profiles.schema_version_must_be_4.0")
    if str(profiles.get("setup_script_policy") or "") != "never_execute":
        errors.append("profiles.setup_script_policy_must_be_never_execute")
    runtime_profiles = _dict(profiles.get("runtime_profiles"))
    for version, runtime_value in runtime_profiles.items():
        runtime = _dict(runtime_value)
        relative = str(runtime.get("relative_executable") or "")
        if relative and not _safe_relative_path(relative):
            errors.append(f"profiles.runtime_path_is_unsafe:{version}")
        for platform, platform_relative_value in _dict(
            runtime.get("relative_executables")
        ).items():
            platform_relative = str(platform_relative_value or "")
            if platform not in SUPPORTED_EXECUTION_PLATFORMS - {"any"}:
                errors.append(
                    f"profiles.runtime_platform_is_invalid:{version}:{platform}"
                )
            if platform_relative and not _safe_relative_path(platform_relative):
                errors.append(
                    f"profiles.runtime_path_is_unsafe:{version}:{platform}"
                )
    project_profiles = _dict(profiles.get("project_profiles"))
    for project, profile_value in project_profiles.items():
        profile = _dict(profile_value)
        if profile.get("execute_benchmark_setup_script") is not False:
            errors.append(f"profiles.setup_script_execution_must_be_false:{project}")
        if profile.get("dependency_install_requires_authorization") is not True:
            errors.append(
                f"profiles.dependency_install_authorization_must_be_true:{project}"
            )
        required_platform = str(
            profile.get("required_execution_platform") or "any"
        )
        if required_platform not in SUPPORTED_EXECUTION_PLATFORMS:
            errors.append(
                f"profiles.required_execution_platform_is_invalid:{project}"
            )
        for module in _list(profile.get("required_runtime_modules")):
            if not SAFE_MODULE_PATTERN.fullmatch(str(module)):
                errors.append(f"profiles.required_module_is_invalid:{project}:{module}")
        for platform, modules_value in _dict(
            profile.get("required_runtime_modules_by_platform")
        ).items():
            if platform not in SUPPORTED_EXECUTION_PLATFORMS - {"any"}:
                errors.append(
                    f"profiles.required_module_platform_is_invalid:"
                    f"{project}:{platform}"
                )
            for module in _list(modules_value):
                if not SAFE_MODULE_PATTERN.fullmatch(str(module)):
                    errors.append(
                        f"profiles.required_module_is_invalid:"
                        f"{project}:{platform}:{module}"
                    )
        for field in ("pythonpath_entries", "optional_pythonpath_entries"):
            for index, entry_value in enumerate(_list(profile.get(field))):
                entry = str(entry_value)
                if not _safe_relative_path(entry):
                    errors.append(
                        f"profiles.pythonpath_entry_is_unsafe:"
                        f"{project}:{field}:{index}"
                    )
        preparation_paths: set[str] = set()
        for index, file_value in enumerate(_list(profile.get("preparation_files"))):
            preparation_file = _dict(file_value)
            path = str(preparation_file.get("path") or "")
            content = preparation_file.get("content")
            reason = str(preparation_file.get("reason") or "")
            expected_sha256 = str(preparation_file.get("sha256") or "").lower()
            if not _safe_relative_path(path):
                errors.append(
                    f"profiles.preparation_file_path_is_unsafe:{project}:{index}"
                )
            normalized_path = PurePosixPath(path.replace("\\", "/")).as_posix()
            if normalized_path in preparation_paths:
                errors.append(
                    f"profiles.preparation_file_path_is_duplicate:{project}:{index}"
                )
            preparation_paths.add(normalized_path)
            if not isinstance(content, str) or len(content.encode("utf-8")) > 4096:
                errors.append(
                    f"profiles.preparation_file_content_is_invalid:{project}:{index}"
                )
            elif (
                not SHA256_PATTERN.fullmatch(expected_sha256)
                or hashlib.sha256(content.encode("utf-8")).hexdigest()
                != expected_sha256
            ):
                errors.append(
                    f"profiles.preparation_file_sha256_is_invalid:{project}:{index}"
                )
            if not reason:
                errors.append(
                    f"profiles.preparation_file_reason_is_required:{project}:{index}"
                )
            source_path = str(preparation_file.get("source_path") or "")
            source_text_sha256 = str(
                preparation_file.get("source_text_sha256") or ""
            ).lower()
            if bool(source_path) != bool(source_text_sha256):
                errors.append(
                    f"profiles.preparation_source_assertion_is_incomplete:"
                    f"{project}:{index}"
                )
            elif source_path and (
                not _safe_relative_path(source_path)
                or not SHA256_PATTERN.fullmatch(source_text_sha256)
            ):
                errors.append(
                    f"profiles.preparation_source_assertion_is_invalid:"
                    f"{project}:{index}"
                )
        seen_plugins: set[str] = set()
        for index, plugin_value in enumerate(
            _list(profile.get("repository_pytest_plugins"))
        ):
            plugin = str(plugin_value)
            if (
                not SAFE_PYTHON_MODULE_PATTERN.fullmatch(plugin)
                or plugin in seen_plugins
            ):
                errors.append(
                    f"profiles.repository_pytest_plugin_is_invalid:"
                    f"{project}:{index}"
                )
            elif not _profile_materializes_python_module(profile, plugin):
                errors.append(
                    f"profiles.repository_pytest_plugin_is_not_materialized:"
                    f"{project}:{index}"
                )
            seen_plugins.add(plugin)
        for index, rewrite_value in enumerate(
            _list(profile.get("command_module_rewrites"))
        ):
            rewrite = _dict(rewrite_value)
            source = str(rewrite.get("from") or "")
            target = str(rewrite.get("to") or "")
            if source not in ADAPTABLE_MODULES or target not in SUPPORTED_EXECUTION_MODULES:
                errors.append(f"profiles.command_rewrite_is_invalid:{project}:{index}")
            if not str(rewrite.get("reason") or ""):
                errors.append(f"profiles.command_rewrite_reason_is_required:{project}:{index}")
    return errors


def build_reproduction_plan(
    *,
    catalog: dict[str, Any],
    selection_plan: dict[str, Any],
    profiles: dict[str, Any],
    runtime_root: str | Path,
    splits: set[str] | None = None,
    projects: set[str] | None = None,
    case_ids: set[str] | None = None,
    limit: int | None = None,
    runtime_probe: RuntimeProbe | None = None,
    execution_platform: str | None = None,
) -> dict[str, Any]:
    catalog_audit = validate_v4_catalog(catalog)
    if catalog_audit["status"] != "pass":
        raise ValueError("Catalog audit failed: " + ";".join(catalog_audit["errors"]))
    profile_errors = validate_reproduction_profiles(profiles)
    if profile_errors:
        raise ValueError("Invalid reproduction profiles: " + ";".join(profile_errors))
    plan_errors = validate_selection_plan(
        selection_plan,
        inventory=_inventory_projection(catalog),
    )
    plan_errors = [
        error
        for error in plan_errors
        if error
        not in {
            "selection_plan.inventory_sha256_mismatch",
            "selection_plan.target_contract_requires_v3_catalog",
        }
    ]
    if plan_errors:
        raise ValueError("Invalid selection plan structure: " + ";".join(plan_errors))

    root = Path(runtime_root).resolve()
    reproducible_by_id = {
        str(case.get("case_id") or ""): case
        for case in [_dict(item) for item in _list(catalog.get("cases"))]
        if case.get("status") in {"candidate", "accepted"}
    }
    items: list[dict[str, Any]] = []
    sequence = 0
    probe = runtime_probe or probe_runtime
    observed_platform = execution_platform or _current_execution_platform()
    project_profiles = _dict(profiles.get("project_profiles"))
    runtime_profiles = _dict(profiles.get("runtime_profiles"))
    probe_cache: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}
    for project_value in _list(selection_plan.get("projects")):
        project_plan = _dict(project_value)
        project = str(project_plan.get("name") or "")
        split = str(project_plan.get("benchmark_split") or "")
        if splits and split not in splits:
            continue
        if projects and project not in projects:
            continue
        project_profile = _dict(project_profiles.get(project))
        for project_index, bug_id_value in enumerate(
            _list(project_plan.get("candidate_bug_ids")), start=1
        ):
            case_id = f"bugsinpy-{project.lower()}-{int(bug_id_value)}"
            if case_ids and case_id not in case_ids:
                continue
            case = reproducible_by_id.get(case_id)
            if case is None:
                raise ValueError(
                    f"Selection plan reproducible case missing from catalog: {case_id}"
                )
            sequence += 1
            adapted, adaptation = adapt_v4_case_for_reproduction(
                case,
                project_profile=project_profile,
            )
            version = str(_dict(case.get("environment")).get("python_version") or "")
            runtime_config = _project_runtime_config(
                project_profile,
                version=version,
                fallback=_runtime_config_for_platform(
                    _dict(runtime_profiles.get(version)),
                    observed_platform,
                ),
                execution_platform=observed_platform,
            )
            runtime = _resolve_runtime(root, version, runtime_config)
            modules = _required_runtime_modules(
                project_profile,
                observed_platform,
            )
            if runtime["status"] == "available":
                probe_key = (
                    str(runtime["python_executable"]),
                    version,
                    tuple(modules),
                )
                if probe_key not in probe_cache:
                    probe_cache[probe_key] = probe(
                        Path(runtime["python_executable"]),
                        version,
                        modules,
                    )
                runtime["probe"] = copy.deepcopy(probe_cache[probe_key])
            else:
                runtime["probe"] = {
                    "status": "not_run",
                    "reason": "runtime_unavailable",
                    "missing_modules": modules,
                }
            readiness, blockers = _reproduction_readiness(
                adaptation=adaptation,
                runtime=runtime,
                project_profile=project_profile,
                execution_platform=observed_platform,
            )
            items.append(
                {
                    "sequence": sequence,
                    "project_sequence": project_index,
                    "case_id": case_id,
                    "catalog_status": str(case.get("status") or ""),
                    "project": project,
                    "owner_repo": str(project_plan.get("owner_repo") or ""),
                    "benchmark_split": split,
                    "target_accepted_count": int(
                        project_plan.get("target_accepted_count") or 0
                    ),
                    "bug_commit_sha": str(case.get("bug_commit_sha") or ""),
                    "fix_commit_sha": str(case.get("fix_commit_sha") or ""),
                    "readiness": readiness,
                    "blockers": blockers,
                    "runtime": runtime,
                    "adaptation": adaptation,
                    "execution_contract": {
                        "setup_script_executed": False,
                        "gold_patch_visible": False,
                        "required_execution_platform": str(
                            project_profile.get("required_execution_platform")
                            or "any"
                        ),
                        "observed_execution_platform": observed_platform,
                        "test_overlay_paths": copy.deepcopy(
                            adapted.get("test_overlay_paths", [])
                        ),
                        "targeted_test_commands": copy.deepcopy(
                            adapted.get("targeted_test_commands", [])
                        ),
                        "regression_command": copy.deepcopy(
                            adapted.get("regression_command", [])
                        ),
                        "preparation_files": [
                            {
                                key: copy.deepcopy(_dict(file_value).get(key))
                                for key in (
                                    "path",
                                    "sha256",
                                    "reason",
                                    "source_path",
                                    "source_text_sha256",
                                )
                                if _dict(file_value).get(key) not in (None, "")
                            }
                            for file_value in _list(
                                adapted.get("preparation_files")
                            )
                        ],
                        "test_environment": copy.deepcopy(
                            _dict(adapted.get("test_environment"))
                        ),
                    },
                }
            )
            if limit is not None and len(items) >= max(0, limit):
                break
        if limit is not None and len(items) >= max(0, limit):
            break
    result = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": "v4-real-bug-reproduction-plan",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog_manifest_sha256": str(catalog.get("manifest_sha256") or ""),
        "selection_plan_sha256": canonical_json_sha256(selection_plan),
        "profiles_sha256": canonical_json_sha256(profiles),
        "runtime_root_committed": False,
        "repository_setup_scripts_executed": False,
        "execution_platform": observed_platform,
        "filters": {
            "splits": sorted(splits or set()),
            "projects": sorted(projects or set()),
            "case_ids": sorted(case_ids or set()),
            "limit": limit,
        },
        "items": items,
        "summary": _summarize_plan(items),
    }
    result["plan_sha256"] = reproduction_plan_fingerprint(result)
    return result


def adapt_v4_case_for_reproduction(
    case: dict[str, Any],
    *,
    project_profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    rewrites = {
        str(_dict(item).get("from") or ""): _dict(item)
        for item in _list(project_profile.get("command_module_rewrites"))
    }
    adapted_commands: list[list[str]] = []
    applied_rewrites: list[dict[str, str]] = []
    errors: list[str] = []
    for command_value in _list(case.get("targeted_tests")):
        command = [str(part) for part in _list(command_value)]
        if len(command) < 3 or command[:2] != ["{python}", "-m"]:
            errors.append("targeted_command_shape_invalid")
            continue
        source_module = command[2]
        rewrite = rewrites.get(source_module)
        if rewrite:
            command[2] = str(rewrite.get("to") or "")
            applied_rewrites.append(
                {
                    "from": source_module,
                    "to": command[2],
                    "reason": str(rewrite.get("reason") or ""),
                }
            )
        if not _safe_test_command(command):
            errors.append(f"targeted_command_module_requires_adapter:{source_module}")
            continue
        adapted_commands.append(command)
    regression_values = _list(case.get("regression_tests"))
    if len(regression_values) != 1:
        errors.append("exactly_one_regression_command_is_required")
        regression_command: list[str] = []
    else:
        regression_command = [str(part) for part in _list(regression_values[0])]
        if not _safe_test_command(regression_command):
            errors.append("regression_command_is_unsupported")
    environment = _dict(case.get("environment"))
    overlay_paths = [str(path) for path in _list(environment.get("declared_test_paths"))]
    if not overlay_paths or any(not _safe_relative_path(path) for path in overlay_paths):
        errors.append("test_overlay_paths_are_missing_or_unsafe")
    test_environment = {
        "pythonpath_entries": [
            str(value) for value in _list(project_profile.get("pythonpath_entries"))
        ],
        "optional_pythonpath_entries": [
            str(value)
            for value in _list(project_profile.get("optional_pythonpath_entries"))
        ],
        "required_tools": [
            str(value) for value in _list(project_profile.get("required_tools"))
        ],
        "repository_pytest_plugins": [
            str(value)
            for value in _list(project_profile.get("repository_pytest_plugins"))
        ],
    }
    adapted = {
        "case_id": str(case.get("case_id") or ""),
        "repository": copy.deepcopy(_dict(case.get("repository"))),
        "bug_commit_sha": str(case.get("bug_commit_sha") or ""),
        "fix_commit_sha": str(case.get("fix_commit_sha") or ""),
        "python_version": str(environment.get("python_version") or ""),
        "test_overlay_paths": overlay_paths,
        "preparation_files": copy.deepcopy(
            _list(project_profile.get("preparation_files"))
        ),
        "targeted_test_commands": adapted_commands,
        "regression_command": regression_command,
        "test_environment": test_environment,
    }
    return adapted, {
        "status": "pass" if not errors else "adapter_required",
        "errors": errors,
        "applied_command_rewrites": applied_rewrites,
        "preparation_file_count": len(
            _list(project_profile.get("preparation_files"))
        ),
        "repository_pytest_plugins": copy.deepcopy(
            test_environment["repository_pytest_plugins"]
        ),
        "benchmark_setup_script_executed": False,
        "test_overlay_mode": "copy_declared_tests_from_fix_to_bug",
    }


def probe_runtime(
    python_executable: Path,
    expected_version: str,
    modules: list[str],
) -> dict[str, Any]:
    version = audit_python_runtime(
        python_executable,
        expected_version=expected_version,
    )
    if version["status"] != "pass":
        return {
            "status": "fail",
            "reason": str(version.get("reason") or "runtime_probe_failed"),
            "version": version,
            "available_modules": [],
            "missing_modules": modules,
        }
    script = (
        "import importlib.util,json,sys;"
        "mods=sys.argv[1:];"
        "print(json.dumps({m:(importlib.util.find_spec(m) is not None) for m in mods}))"
    )
    environment, _ = build_restricted_environment()
    try:
        completed = subprocess.run(
            [str(python_executable), "-c", script, *modules],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=environment,
        )
        observed = json.loads(str(completed.stdout or "{}")) if completed.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {
            "status": "fail",
            "reason": "module_probe_failed",
            "version": version,
            "available_modules": [],
            "missing_modules": modules,
            "error": type(exc).__name__,
        }
    available = sorted(module for module in modules if observed.get(module) is True)
    missing = sorted(module for module in modules if observed.get(module) is not True)
    return {
        "status": "pass" if not missing else "missing_dependencies",
        "reason": "runtime_ready" if not missing else "runtime_modules_missing",
        "version": version,
        "available_modules": available,
        "missing_modules": missing,
    }


def run_reproduction_case(
    *,
    case: dict[str, Any],
    plan_item: dict[str, Any],
    project_profile: dict[str, Any],
    output_dir: str | Path,
    checkout=checkout_github_repository,
    targeted_timeout: int = 120,
    regression_timeout: int = 900,
    runner=None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    case_id = str(case.get("case_id") or "")
    if case_id != str(plan_item.get("case_id") or ""):
        raise ValueError("Plan item and catalog case identity differ.")
    if str(plan_item.get("readiness") or "") != "ready":
        result = _blocked_evidence(
            case,
            plan_item=plan_item,
            started_at=started_at,
            reason="reproduction_preconditions_not_met",
        )
        write_reproduction_evidence(result, output_dir)
        return result
    adapted, adaptation = adapt_v4_case_for_reproduction(
        case,
        project_profile=project_profile,
    )
    if adaptation["status"] != "pass":
        raise ValueError("A ready plan item cannot require command adaptation.")
    preparation = prepare_real_bug_case(
        adapted,
        output_dir,
        checkout=checkout,
        checkout_timeout=180,
    )
    v3_result = reproduce_real_bug_case(
        adapted,
        preparation,
        python_executable=str(_dict(plan_item.get("runtime")).get("python_executable") or ""),
        targeted_timeout=targeted_timeout,
        regression_timeout=regression_timeout,
        runner=runner,
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": f"v4-reproduction:{case_id}",
        "case_id": case_id,
        "bug_commit_sha": str(case.get("bug_commit_sha") or ""),
        "fix_commit_sha": str(case.get("fix_commit_sha") or ""),
        "status": "pass" if v3_result.get("status") == "pass" else "fail",
        "reason": str(v3_result.get("reason") or ""),
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "runtime": copy.deepcopy(_dict(v3_result.get("runtime"))),
        "preparation": copy.deepcopy(_dict(v3_result.get("preparation"))),
        "bug_targeted": copy.deepcopy(_dict(v3_result.get("bug_targeted"))),
        "fix_targeted": copy.deepcopy(_dict(v3_result.get("fix_targeted"))),
        "fix_full_regression": copy.deepcopy(
            _dict(v3_result.get("fix_full_regression"))
        ),
        "acceptance": copy.deepcopy(_dict(v3_result.get("acceptance"))),
        "blocker": copy.deepcopy(_dict(v3_result.get("blocker"))),
        "execution_contract": {
            "benchmark_setup_script_executed": False,
            "gold_patch_visible_to_execution": False,
            "model_calls": 0,
            "adaptation": adaptation,
        },
    }
    result["evidence_sha256"] = reproduction_evidence_fingerprint(result)
    write_reproduction_evidence(result, output_dir)
    return result


def reproduction_plan_fingerprint(plan: dict[str, Any]) -> str:
    value = copy.deepcopy(plan)
    value.pop("generated_at", None)
    value.pop("plan_sha256", None)
    return canonical_json_sha256(value)


def reproduction_evidence_fingerprint(evidence: dict[str, Any]) -> str:
    value = copy.deepcopy(evidence)
    value.pop("evidence_sha256", None)
    return canonical_json_sha256(value)


def write_reproduction_plan(plan: dict[str, Any], output: str | Path) -> str:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_lf(path, json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    return str(path)


def write_reproduction_evidence(
    evidence: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "v4_reproduction.json"
    markdown_path = root / "v4_reproduction.md"
    _write_lf(json_path, json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    _write_lf(markdown_path, render_reproduction_evidence(evidence))
    return {"json": str(json_path), "markdown": str(markdown_path)}


def render_reproduction_evidence(evidence: dict[str, Any]) -> str:
    acceptance = _dict(evidence.get("acceptance"))
    blocker = _dict(evidence.get("blocker"))
    lines = [
        f"# V4 Reproduction: {evidence.get('case_id', '')}",
        "",
        f"- Status: `{evidence.get('status', '')}`",
        f"- Reason: `{evidence.get('reason', '')}`",
        f"- Bug targeted failed: `{acceptance.get('bug_targeted_failed', False)}`",
        f"- Fix targeted passed: `{acceptance.get('fix_targeted_passed', False)}`",
        f"- Fix regression passed: `{acceptance.get('fix_full_regression_passed', False)}`",
        f"- Reproducible: `{acceptance.get('reproducible', False)}`",
        f"- Blocker layer: `{blocker.get('layer', '')}`",
        f"- Blocker category: `{blocker.get('category', '')}`",
        f"- Evidence SHA-256: `{evidence.get('evidence_sha256', '')}`",
        "",
    ]
    return "\n".join(lines)


def _resolve_runtime(
    root: Path,
    version: str,
    runtime_config: dict[str, Any],
) -> dict[str, Any]:
    relative = str(runtime_config.get("relative_executable") or "")
    if not relative:
        return {
            "status": "unmapped",
            "reason": "exact_runtime_not_mapped",
            "expected_version": version,
            "python_executable": "",
        }
    if not _safe_relative_path(relative):
        return {
            "status": "unsafe",
            "reason": "runtime_path_is_unsafe",
            "expected_version": version,
            "python_executable": "",
        }
    executable = (root / Path(*PurePosixPath(relative.replace("\\", "/")).parts)).resolve()
    if not _within(executable, root) or not executable.is_file() or executable.is_symlink():
        return {
            "status": "missing",
            "reason": "exact_runtime_executable_missing",
            "expected_version": version,
            "python_executable": str(executable),
        }
    return {
        "status": "available",
        "reason": "exact_runtime_executable_present",
        "expected_version": version,
        "python_executable": str(executable),
        "relative_executable": PurePosixPath(relative.replace("\\", "/")).as_posix(),
    }


def _project_runtime_config(
    project_profile: dict[str, Any],
    *,
    version: str,
    fallback: dict[str, Any],
    execution_platform: str,
) -> dict[str, Any]:
    template = str(project_profile.get("isolated_environment_template") or "")
    if not template:
        return copy.deepcopy(fallback)
    environment_path = template.format(version=version)
    executable = (
        PurePosixPath(environment_path) / "Scripts" / "python.exe"
        if execution_platform == "windows"
        else PurePosixPath(environment_path) / "bin" / "python"
    )
    return {"relative_executable": executable.as_posix()}


def _runtime_config_for_platform(
    runtime_profile: dict[str, Any],
    execution_platform: str,
) -> dict[str, Any]:
    platform_relative = str(
        _dict(runtime_profile.get("relative_executables")).get(
            execution_platform
        )
        or ""
    )
    if platform_relative:
        return {"relative_executable": platform_relative}
    return {
        "relative_executable": str(
            runtime_profile.get("relative_executable") or ""
        )
    }


def _required_runtime_modules(
    project_profile: dict[str, Any],
    execution_platform: str,
) -> list[str]:
    values = [
        *_list(project_profile.get("required_runtime_modules")),
        *_list(
            _dict(project_profile.get("required_runtime_modules_by_platform")).get(
                execution_platform
            )
        ),
    ]
    return sorted({str(value) for value in values if str(value)})


def _reproduction_readiness(
    *,
    adaptation: dict[str, Any],
    runtime: dict[str, Any],
    project_profile: dict[str, Any],
    execution_platform: str,
) -> tuple[str, list[str]]:
    blockers: list[str] = []
    if adaptation.get("status") != "pass":
        blockers.extend(str(item) for item in _list(adaptation.get("errors")))
    if runtime.get("status") != "available":
        blockers.append(str(runtime.get("reason") or "runtime_unavailable"))
    probe = _dict(runtime.get("probe"))
    if runtime.get("status") == "available" and probe.get("status") != "pass":
        blockers.append(str(probe.get("reason") or "runtime_probe_failed"))
    if project_profile.get("native_build_adapter_required") is True:
        blockers.append("native_build_adapter_required")
    required_platform = str(
        project_profile.get("required_execution_platform") or "any"
    )
    if required_platform != "any" and required_platform != execution_platform:
        blockers.append(
            "execution_platform_mismatch:"
            f"required_{required_platform}:observed_{execution_platform}"
        )
    return ("ready", []) if not blockers else ("blocked", sorted(set(blockers)))


def _current_execution_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    return sys.platform


def _summarize_plan(items: list[dict[str, Any]]) -> dict[str, Any]:
    blockers: dict[str, int] = {}
    for item in items:
        for blocker in _list(item.get("blockers")):
            key = str(blocker)
            blockers[key] = blockers.get(key, 0) + 1
    return {
        "case_count": len(items),
        "ready_count": sum(item.get("readiness") == "ready" for item in items),
        "blocked_count": sum(item.get("readiness") != "ready" for item in items),
        "catalog_status_counts": {
            status: sum(item.get("catalog_status") == status for item in items)
            for status in ("candidate", "accepted")
        },
        "split_counts": {
            split: sum(item.get("benchmark_split") == split for item in items)
            for split in ("development", "validation", "test")
        },
        "blocker_counts": dict(sorted(blockers.items())),
    }


def _blocked_evidence(
    case: dict[str, Any],
    *,
    plan_item: dict[str, Any],
    started_at: str,
    reason: str,
) -> dict[str, Any]:
    result = {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": f"v4-reproduction:{case.get('case_id', '')}",
        "case_id": str(case.get("case_id") or ""),
        "bug_commit_sha": str(case.get("bug_commit_sha") or ""),
        "fix_commit_sha": str(case.get("fix_commit_sha") or ""),
        "status": "blocked",
        "reason": reason,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "runtime": copy.deepcopy(_dict(plan_item.get("runtime"))),
        "preparation": {"status": "not_run"},
        "bug_targeted": {"status": "not_run", "results": []},
        "fix_targeted": {"status": "not_run", "results": []},
        "fix_full_regression": {"status": "not_run", "results": []},
        "acceptance": {
            "bug_targeted_failed": False,
            "fix_targeted_passed": False,
            "fix_full_regression_passed": False,
            "reproducible": False,
        },
        "blocker": {
            "layer": "environment",
            "category": "preconditions",
            "reasons": copy.deepcopy(_list(plan_item.get("blockers"))),
        },
        "execution_contract": {
            "benchmark_setup_script_executed": False,
            "gold_patch_visible_to_execution": False,
            "model_calls": 0,
        },
    }
    result["evidence_sha256"] = reproduction_evidence_fingerprint(result)
    return result


def _inventory_projection(catalog: dict[str, Any]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for case in [_dict(value) for value in _list(catalog.get("cases"))]:
        if case.get("status") not in {"candidate", "accepted"}:
            continue
        provenance = _dict(case.get("provenance"))
        benchmark_case = str(provenance.get("benchmark_case") or "")
        project, _, bug_id_text = benchmark_case.partition(":")
        cases.append(
            {
                "project": project,
                "bug_id": int(bug_id_text),
                "repository": copy.deepcopy(_dict(case.get("repository"))),
                "bug_commit_sha": str(case.get("bug_commit_sha") or ""),
                "eligibility": {"status": "eligible"},
            }
        )
    return {
        "inventory_sha256": "catalog_projection",
        "cases": cases,
    }


def _profile_materializes_python_module(
    profile: dict[str, Any],
    module: str,
) -> bool:
    preparation_paths = {
        PurePosixPath(str(_dict(item).get("path") or "").replace("\\", "/")).as_posix()
        for item in _list(profile.get("preparation_files"))
        if str(_dict(item).get("path") or "")
    }
    module_path = PurePosixPath(*module.split("."))
    for entry_value in _list(profile.get("pythonpath_entries")):
        entry = str(entry_value)
        if not _safe_relative_path(entry):
            continue
        root = PurePosixPath(entry.replace("\\", "/"))
        if root.as_posix() != REPOSITORY_RUNTIME_SUPPORT_ROOT:
            continue
        candidates = {
            (root / module_path).with_suffix(".py").as_posix(),
            (root / module_path / "__init__.py").as_posix(),
        }
        if preparation_paths.intersection(candidates):
            return True
    return False


def _safe_test_command(command: list[str]) -> bool:
    return (
        len(command) >= 3
        and command[:2] == ["{python}", "-m"]
        and command[2] in SUPPORTED_EXECUTION_MODULES
        and all(
            part
            and not any(
                token in part
                for token in ("&&", "||", ";", "|", ">", "<", "`", "$(")
            )
            and "\x00" not in part
            and "\n" not in part
            and "\r" not in part
            for part in command
        )
    )


def _safe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("//"):
        return False
    if len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":":
        return False
    pure = PurePosixPath(normalized)
    return not pure.is_absolute() and ".." not in pure.parts


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _write_lf(path: Path, content: str) -> None:
    path.write_bytes(content.replace("\r\n", "\n").encode("utf-8"))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or run V4 real-bug reproduction without benchmark setup scripts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("catalog")
    plan.add_argument("selection_plan")
    plan.add_argument("profiles")
    plan.add_argument("output")
    plan.add_argument("--runtime-root", required=True)
    plan.add_argument("--split", action="append", default=[])
    plan.add_argument("--project", action="append", default=[])
    plan.add_argument("--case-id", action="append", default=[])
    plan.add_argument("--limit", type=int)
    run = subparsers.add_parser("run")
    run.add_argument("catalog")
    run.add_argument("selection_plan")
    run.add_argument("profiles")
    run.add_argument("output_dir")
    run.add_argument("--runtime-root", required=True)
    run.add_argument("--case-id", required=True)
    run.add_argument("--targeted-timeout", type=int, default=120)
    run.add_argument("--regression-timeout", type=int, default=900)
    run.add_argument("--require-pass", action="store_true")
    accept = subparsers.add_parser("accept")
    accept.add_argument("catalog")
    accept.add_argument("artifact_archive")
    accept.add_argument("attestation")
    accept.add_argument("catalog_output")
    accept.add_argument("audit_output")
    accept.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    catalog = load_json_object(args.catalog)
    if args.command == "accept":
        from code_intelligence_agent.evaluation.v4_real_bug_evidence import (
            accept_v4_reproduction_artifact,
            write_v4_acceptance_artifacts,
        )

        accepted_catalog, audit = accept_v4_reproduction_artifact(
            catalog,
            args.artifact_archive,
            load_json_object(args.attestation),
        )
        paths = write_v4_acceptance_artifacts(
            accepted_catalog,
            audit,
            catalog_output=args.catalog_output,
            audit_output=args.audit_output,
        )
        print(json.dumps({"audit": audit, "paths": paths}, indent=2, ensure_ascii=False))
        if args.require_pass and audit["status"] != "pass":
            raise SystemExit(1)
        return
    selection_plan = load_json_object(args.selection_plan)
    profiles = load_json_object(args.profiles)
    if args.command == "plan":
        plan = build_reproduction_plan(
            catalog=catalog,
            selection_plan=selection_plan,
            profiles=profiles,
            runtime_root=args.runtime_root,
            splits=set(args.split),
            projects=set(args.project),
            case_ids=set(args.case_id),
            limit=args.limit,
        )
        write_reproduction_plan(plan, args.output)
        print(json.dumps(plan["summary"], indent=2, ensure_ascii=False))
        return
    plan = build_reproduction_plan(
        catalog=catalog,
        selection_plan=selection_plan,
        profiles=profiles,
        runtime_root=args.runtime_root,
        case_ids={args.case_id},
    )
    if not plan["items"]:
        raise SystemExit(f"Unknown reproducible case: {args.case_id}")
    case = next(
        item
        for item in _list(catalog.get("cases"))
        if str(_dict(item).get("case_id") or "") == args.case_id
    )
    project = str(_dict(plan["items"][0]).get("project") or "")
    profile = _dict(_dict(profiles.get("project_profiles")).get(project))
    result = run_reproduction_case(
        case=_dict(case),
        plan_item=_dict(plan["items"][0]),
        project_profile=profile,
        output_dir=args.output_dir,
        targeted_timeout=args.targeted_timeout,
        regression_timeout=args.regression_timeout,
    )
    print(render_reproduction_evidence(result))
    if args.require_pass and result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
