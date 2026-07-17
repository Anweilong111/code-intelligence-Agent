from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import re
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from code_intelligence_agent.evaluation.v3_experiment_protocol import (
    canonical_json_sha256,
)
from code_intelligence_agent.evaluation.v3_real_bug_reproduction import (
    audit_python_runtime,
)
from code_intelligence_agent.evaluation.v4_real_bug_benchmark import load_json_object
from code_intelligence_agent.evaluation.v4_real_bug_reproduction import (
    probe_runtime,
    resolve_project_runtime_variant,
    validate_reproduction_profiles,
)
from code_intelligence_agent.tools.runtime_security import build_restricted_environment


SCHEMA_VERSION = "4.0"
PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
Runner = Callable[..., subprocess.CompletedProcess]
RuntimeProbe = Callable[[Path, str, list[str]], dict[str, Any]]
ArchiveFetcher = Callable[[str, str, int], bytes]
PURE_PYTHON_ARCHIVE_HOSTS = {"files.pythonhosted.org"}
CONDA_BINARY_ARCHIVE_HOSTS = {"conda.anaconda.org"}
MAX_MANUAL_ARCHIVE_SIZE = 50 * 1024 * 1024
MAX_MANUAL_ARCHIVE_EXPANDED_SIZE = 100 * 1024 * 1024
MAX_MANUAL_ARCHIVE_MEMBERS = 10000
CONDA_DIST_INFO_FILES = {
    "INSTALLER",
    "LICENSE",
    "METADATA",
    "RECORD",
    "WHEEL",
    "entry_points.txt",
    "namespace_packages.txt",
    "top_level.txt",
}


def validate_bootstrap_requirements(requirements: list[str]) -> list[str]:
    errors: list[str] = []
    names: set[str] = set()
    if not requirements:
        return ["bootstrap_requirements_are_required"]
    for index, raw_value in enumerate(requirements):
        value = str(raw_value).strip()
        try:
            requirement = Requirement(value)
        except InvalidRequirement:
            errors.append(f"invalid_requirement:{index}")
            continue
        name = str(canonicalize_name(requirement.name))
        if not PACKAGE_NAME_PATTERN.fullmatch(requirement.name):
            errors.append(f"invalid_package_name:{index}")
        if name in names:
            errors.append(f"duplicate_package:{name}")
        names.add(name)
        if requirement.url:
            errors.append(f"direct_url_forbidden:{name}")
        if requirement.extras:
            errors.append(f"extras_forbidden:{name}")
        if requirement.marker:
            errors.append(f"environment_marker_forbidden:{name}")
        specifiers = list(requirement.specifier)
        if (
            len(specifiers) != 1
            or specifiers[0].operator != "=="
            or "*" in specifiers[0].version
        ):
            errors.append(f"exact_version_required:{name}")
    return errors


def validate_manual_python_archives(archives: list[Any]) -> list[str]:
    errors: list[str] = []
    archive_ids: set[str] = set()
    package_names: set[str] = set()
    for index, raw_archive in enumerate(archives):
        archive = _dict(raw_archive)
        archive_id = str(archive.get("archive_id") or "")
        package = str(archive.get("package") or "")
        version = str(archive.get("version") or "")
        url = str(archive.get("url") or "")
        sha256 = str(archive.get("sha256") or "")
        expected_size = archive.get("size")
        source_root = str(archive.get("source_root") or "")
        members = [str(value) for value in _list(archive.get("install_members"))]
        excluded_members = [
            str(value) for value in _list(archive.get("exclude_members"))
        ]
        platforms = [str(value) for value in _list(archive.get("platforms"))]
        archive_type = str(archive.get("archive_type") or "")
        artifact_kind = str(archive.get("artifact_kind") or "pure_python")
        prefix = f"manual_archive:{index}"
        if (
            not PACKAGE_NAME_PATTERN.fullmatch(archive_id)
            or archive_id in archive_ids
        ):
            errors.append(f"{prefix}:archive_id_is_missing_or_duplicate")
        archive_ids.add(archive_id)
        if not package or not PACKAGE_NAME_PATTERN.fullmatch(package):
            errors.append(f"{prefix}:package_name_is_invalid")
        normalized_package = str(canonicalize_name(package))
        if normalized_package in package_names:
            errors.append(f"{prefix}:package_is_duplicated")
        package_names.add(normalized_package)
        if not version or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", version):
            errors.append(f"{prefix}:version_is_invalid")
        parsed = urlparse(url)
        allowed_hosts = (
            PURE_PYTHON_ARCHIVE_HOSTS
            if archive_type == "zip"
            else CONDA_BINARY_ARCHIVE_HOSTS
            if archive_type == "conda-tar-bz2"
            else set()
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname not in allowed_hosts
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            errors.append(f"{prefix}:source_url_is_not_allowed")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            errors.append(f"{prefix}:sha256_is_invalid")
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size <= 0
            or expected_size > MAX_MANUAL_ARCHIVE_SIZE
        ):
            errors.append(f"{prefix}:size_is_invalid")
        if archive_type not in {"zip", "conda-tar-bz2"}:
            errors.append(f"{prefix}:archive_type_is_invalid")
        if archive_type == "zip" and artifact_kind != "pure_python":
            errors.append(f"{prefix}:zip_artifact_kind_must_be_pure_python")
        if archive_type == "conda-tar-bz2":
            if artifact_kind != "conda_python_binary":
                errors.append(f"{prefix}:conda_artifact_kind_is_invalid")
            if archive.get("replaces_pip_requirement") is not True:
                errors.append(f"{prefix}:conda_archive_must_replace_requirement")
            if platforms != ["linux"]:
                errors.append(f"{prefix}:conda_archive_platform_must_be_linux")
            if str(archive.get("conda_subdir") or "") != "linux-64":
                errors.append(f"{prefix}:conda_subdir_must_be_linux_64")
            if not re.fullmatch(
                r"[A-Za-z0-9_.+-]+", str(archive.get("conda_build") or "")
            ):
                errors.append(f"{prefix}:conda_build_is_invalid")
            if not re.fullmatch(
                r"cp\d+-cp\d+[a-z]*-linux_x86_64",
                str(archive.get("wheel_tag") or ""),
            ):
                errors.append(f"{prefix}:wheel_tag_is_invalid")
            expected_filename = (
                f"{package}-{version}-{archive.get('conda_build')}.tar.bz2"
            )
            if PurePosixPath(parsed.path).name != expected_filename:
                errors.append(f"{prefix}:conda_url_filename_mismatch")
            if not re.fullmatch(
                r"lib/python\d+\.\d+/site-packages", source_root
            ):
                errors.append(f"{prefix}:conda_source_root_is_invalid")
            dist_info_dir = str(archive.get("dist_info_dir") or "")
            if (
                not _safe_relative_path(dist_info_dir)
                or "/" in dist_info_dir.replace("\\", "/")
                or not dist_info_dir.endswith(".dist-info")
                or dist_info_dir not in members
            ):
                errors.append(f"{prefix}:dist_info_dir_is_invalid")
            native_module_roots = [
                str(value)
                for value in _list(archive.get("native_module_roots"))
            ]
            if (
                not native_module_roots
                or len(set(native_module_roots)) != len(native_module_roots)
                or any(
                    not _safe_relative_path(value)
                    or not any(
                        value == member.rstrip("/")
                        or value.startswith(member.rstrip("/") + "/")
                        for member in members
                        if not member.endswith(".dist-info")
                    )
                    for value in native_module_roots
                )
            ):
                errors.append(f"{prefix}:native_module_roots_are_invalid")
            native_suffixes = [
                str(value)
                for value in _list(archive.get("allowed_native_suffixes"))
            ]
            if (
                not native_suffixes
                or len(set(native_suffixes)) != len(native_suffixes)
                or any(
                    not re.fullmatch(
                        r"\.cpython-\d+[a-z]*-x86_64-linux-gnu\.so", value
                    )
                    for value in native_suffixes
                )
            ):
                errors.append(f"{prefix}:allowed_native_suffixes_are_invalid")
        if not _safe_relative_path(source_root):
            errors.append(f"{prefix}:source_root_is_unsafe")
        if not members:
            errors.append(f"{prefix}:install_members_are_required")
        elif len(set(members)) != len(members):
            errors.append(f"{prefix}:install_members_are_duplicated")
        for member in members:
            if not _safe_relative_path(member):
                errors.append(f"{prefix}:install_member_is_unsafe")
        if len(set(excluded_members)) != len(excluded_members):
            errors.append(f"{prefix}:exclude_members_are_duplicated")
        for member in excluded_members:
            if not _safe_relative_path(member):
                errors.append(f"{prefix}:exclude_member_is_unsafe")
        if platforms and (
            len(set(platforms)) != len(platforms)
            or any(value not in {"darwin", "linux", "windows"} for value in platforms)
        ):
            errors.append(f"{prefix}:platforms_are_invalid")
    return errors


def build_environment_bootstrap_plan(
    *,
    profiles: dict[str, Any],
    project: str,
    python_version: str,
    base_runtime_root: str | Path,
    isolated_runtime_root: str | Path,
    execution_platform: str | None = None,
    case_id: str = "",
) -> dict[str, Any]:
    profile_errors = validate_reproduction_profiles(profiles)
    if profile_errors:
        raise ValueError("Invalid reproduction profiles: " + ";".join(profile_errors))
    project_profile = _dict(_dict(profiles.get("project_profiles")).get(project))
    if not project_profile:
        raise ValueError(f"Unknown project profile: {project}")
    project_profile, runtime_variant = resolve_project_runtime_variant(
        project_profile,
        case_id,
    )
    if runtime_variant["status"] != "pass":
        raise ValueError(
            "Runtime variant resolution failed: "
            + ";".join(_list(runtime_variant.get("errors")))
        )
    observed_platform = execution_platform or _host_execution_platform()
    major_minor = ".".join(python_version.split(".")[:2])
    requirements = [
        str(value) for value in _list(project_profile.get("bootstrap_requirements"))
    ]
    requirement_errors = validate_bootstrap_requirements(requirements)
    if requirement_errors:
        raise ValueError("Unsafe bootstrap requirements: " + ";".join(requirement_errors))
    configured_manual_archives = copy.deepcopy(
        _list(project_profile.get("manual_python_archives"))
    )
    archive_errors = validate_manual_python_archives(configured_manual_archives)
    if archive_errors:
        raise ValueError("Unsafe manual Python archives: " + ";".join(archive_errors))
    manual_archives = [
        archive
        for archive in configured_manual_archives
        if not _list(_dict(archive).get("platforms"))
        or observed_platform in _list(_dict(archive).get("platforms"))
    ]
    requirement_names = {
        str(canonicalize_name(Requirement(value).name)) for value in requirements
    }
    archive_names = {
        str(canonicalize_name(str(_dict(value).get("package") or "")))
        for value in manual_archives
    }
    replacing_archive_names = {
        str(canonicalize_name(str(_dict(value).get("package") or "")))
        for value in manual_archives
        if _dict(value).get("replaces_pip_requirement") is True
    }
    unsupported_overlap = sorted(
        (requirement_names & archive_names) - replacing_archive_names
    )
    if unsupported_overlap:
        raise ValueError(
            "Packages cannot use both pip and manual archive installation: "
            + ",".join(unsupported_overlap)
        )
    requirement_versions = {
        str(canonicalize_name(Requirement(value).name)): list(
            Requirement(value).specifier
        )[0].version
        for value in requirements
    }
    for archive_value in manual_archives:
        archive = _dict(archive_value)
        if archive.get("replaces_pip_requirement") is not True:
            continue
        name = str(canonicalize_name(str(archive.get("package") or "")))
        if name not in requirement_versions:
            raise ValueError(
                f"Manual archive replacement has no matching requirement: {name}"
            )
        if requirement_versions[name] != str(archive.get("version") or ""):
            raise ValueError(
                f"Manual archive replacement version mismatch: {name}"
            )
        if archive.get("artifact_kind") == "conda_python_binary":
            compact_version = major_minor.replace(".", "")
            wheel_parts = str(archive.get("wheel_tag") or "").split("-")
            if (
                str(archive.get("source_root") or "")
                != f"lib/python{major_minor}/site-packages"
                or not str(archive.get("conda_build") or "").startswith(
                    f"py{compact_version}"
                )
                or len(wheel_parts) != 3
                or wheel_parts[0] != f"cp{compact_version}"
                or not wheel_parts[1].startswith(f"cp{compact_version}")
                or any(
                    f".cpython-{compact_version}" not in str(value)
                    for value in _list(archive.get("allowed_native_suffixes"))
                )
            ):
                raise ValueError(
                    f"Manual archive Python ABI mismatch: {name}"
                )
    pip_requirements = [
        value
        for value in requirements
        if str(canonicalize_name(Requirement(value).name))
        not in replacing_archive_names
    ]
    runtime_profile = _dict(
        _dict(profiles.get("runtime_profiles")).get(python_version)
    )
    base_relative = str(
        _dict(runtime_profile.get("relative_executables")).get(
            observed_platform
        )
        or runtime_profile.get("relative_executable")
        or ""
    )
    if not _safe_relative_path(base_relative):
        raise ValueError(f"Exact base runtime is not safely mapped: {python_version}")
    base_root = Path(base_runtime_root).resolve()
    base_python = _resolve_within(base_root, base_relative)
    if not base_python.is_file() or base_python.is_symlink():
        raise ValueError(f"Exact base runtime is missing: {base_python}")

    environment_template = str(
        project_profile.get("isolated_environment_template") or ""
    )
    if not environment_template:
        raise ValueError(f"Isolated environment template is missing: {project}")
    environment_relative = environment_template.format(
        version=python_version,
        case_id=case_id,
    )
    if not _safe_relative_path(environment_relative):
        raise ValueError("Isolated environment path is unsafe.")
    isolated_root = Path(isolated_runtime_root).resolve()
    environment_path = _resolve_within(isolated_root, environment_relative)
    target_python = (
        environment_path / "Scripts" / "python.exe"
        if observed_platform == "windows"
        else environment_path / "bin" / "python"
    )
    site_packages_path = (
        environment_path / "Lib" / "site-packages"
        if observed_platform == "windows"
        else environment_path / "lib" / f"python{major_minor}" / "site-packages"
    )
    create_command = [
        str(base_python),
        "-m",
        "venv",
    ]
    if observed_platform != "windows":
        create_command.append("--copies")
    create_command.append(str(environment_path))
    install_command = [
        str(target_python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--no-deps",
        *pip_requirements,
    ]
    check_command = [str(target_python), "-m", "pip", "check"]
    audit_command = [str(target_python), "-m", "pip", "freeze", "--all"]
    plan = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": (
            f"v4-bootstrap:{project}:{runtime_variant['variant_id']}:"
            f"py{python_version}"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": project,
        "case_id": case_id,
        "runtime_variant": runtime_variant,
        "python_version": python_version,
        "execution_platform": observed_platform,
        "profiles_sha256": canonical_json_sha256(profiles),
        "base_runtime_root": str(base_root),
        "base_python": str(base_python),
        "isolated_runtime_root": str(isolated_root),
        "environment_relative_path": PurePosixPath(
            environment_relative.replace("\\", "/")
        ).as_posix(),
        "environment_path": str(environment_path),
        "target_python": str(target_python),
        "site_packages_path": str(site_packages_path),
        "requirements": requirements,
        "pip_requirements": pip_requirements,
        "manual_python_archives": manual_archives,
        "required_runtime_modules": sorted(
            {
                str(value)
                for value in [
                    *_list(project_profile.get("required_runtime_modules")),
                    *_list(
                        _dict(
                            project_profile.get(
                                "required_runtime_modules_by_platform"
                            )
                        ).get(observed_platform)
                    ),
                ]
                if str(value)
            }
        ),
        "commands": {
            "create_environment": create_command,
            "install_dependencies": install_command,
            "check_dependencies": check_command,
            "audit_frozen_requirements": audit_command,
        },
        "policy": {
            "dependency_source": (
                _expected_dependency_source(manual_archives)
            ),
            "exact_versions_required": True,
            "direct_urls_allowed": False,
            "vcs_requirements_allowed": False,
            "editable_install_allowed": False,
            "repository_setup_script_allowed": False,
            "repository_project_install_allowed": False,
            "manual_archive_install_allowed": bool(manual_archives),
            "hash_pinned_native_archive_install_allowed": any(
                _dict(value).get("artifact_kind") == "conda_python_binary"
                for value in manual_archives
            ),
            "manual_archive_setup_execution_allowed": False,
            "explicit_execution_authorization_required": True,
            "shared_base_runtime_mutation_allowed": False,
        },
    }
    plan["plan_sha256"] = environment_bootstrap_plan_fingerprint(plan)
    return plan


def execute_environment_bootstrap(
    plan: dict[str, Any],
    *,
    authorize_dependency_install: bool,
    proxy_url: str = "",
    create_timeout: int = 180,
    install_timeout: int = 900,
    runner: Runner = subprocess.run,
    runtime_probe: RuntimeProbe = probe_runtime,
    archive_fetcher: ArchiveFetcher | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    validation_errors = validate_environment_bootstrap_plan(plan)
    if validation_errors:
        raise ValueError("Invalid bootstrap plan: " + ";".join(validation_errors))
    if not authorize_dependency_install:
        return _bootstrap_result(
            plan,
            started_at=started_at,
            status="blocked",
            reason="dependency_install_authorization_required",
            commands=[],
            runtime_probe_result={"status": "not_run"},
        )
    proxy_overrides = _validated_proxy_overrides(proxy_url)
    environment_path = Path(str(plan.get("environment_path") or "")).resolve()
    target_python = Path(str(plan.get("target_python") or "")).resolve()
    if environment_path.exists() and not target_python.is_file():
        return _bootstrap_result(
            plan,
            started_at=started_at,
            status="blocked",
            reason="partial_environment_requires_manual_cleanup",
            commands=[],
            runtime_probe_result={"status": "not_run"},
        )
    command_results: list[dict[str, Any]] = []
    if not target_python.is_file():
        environment_path.parent.mkdir(parents=True, exist_ok=True)
        create_env, _ = build_restricted_environment(network_policy="deny")
        create_result = _run_command(
            [str(value) for value in _list(_dict(plan.get("commands")).get("create_environment"))],
            timeout=create_timeout,
            runner=runner,
            environment=create_env,
            stage="create_environment",
        )
        command_results.append(create_result)
        if create_result["status"] != "pass" or not target_python.is_file():
            return _bootstrap_result(
                plan,
                started_at=started_at,
                status="fail",
                reason="isolated_environment_creation_failed",
                commands=command_results,
                runtime_probe_result={"status": "not_run"},
            )
    else:
        command_results.append(
            {
                "stage": "create_environment",
                "status": "skipped",
                "reason": "target_runtime_already_exists",
                "returncode": None,
            }
        )
    install_env, _ = build_restricted_environment(
        overrides=proxy_overrides,
        network_policy="allow",
    )
    install_result = _run_command(
        [
            str(value)
            for value in _list(
                _dict(plan.get("commands")).get("install_dependencies")
            )
        ],
        timeout=install_timeout,
        runner=runner,
        environment=install_env,
        stage="install_dependencies",
    )
    command_results.append(install_result)
    if install_result["status"] != "pass":
        return _bootstrap_result(
            plan,
            started_at=started_at,
            status="fail",
            reason="dependency_install_failed",
            commands=command_results,
            runtime_probe_result={"status": "not_run"},
        )
    fetcher = archive_fetcher or _fetch_manual_archive
    for archive_value in _list(plan.get("manual_python_archives")):
        archive_result = _install_manual_python_archive(
            archive=_dict(archive_value),
            plan=plan,
            proxy_url=proxy_url,
            fetcher=fetcher,
        )
        command_results.append(archive_result)
        if archive_result["status"] != "pass":
            return _bootstrap_result(
                plan,
                started_at=started_at,
                status="fail",
                reason="manual_archive_install_failed",
                commands=command_results,
                runtime_probe_result={"status": "not_run"},
            )
    audit_env, _ = build_restricted_environment(network_policy="deny")
    check_result = _run_command(
        [
            str(value)
            for value in _list(
                _dict(plan.get("commands")).get("check_dependencies")
            )
        ],
        timeout=60,
        runner=runner,
        environment=audit_env,
        stage="check_dependencies",
    )
    command_results.append(check_result)
    if check_result["status"] != "pass":
        return _bootstrap_result(
            plan,
            started_at=started_at,
            status="fail",
            reason="dependency_consistency_check_failed",
            commands=command_results,
            runtime_probe_result={"status": "not_run"},
        )
    frozen_audit = _audit_frozen_requirements(
        plan=plan,
        runner=runner,
        environment=audit_env,
    )
    command_results.append(frozen_audit)
    if frozen_audit["status"] != "pass":
        return _bootstrap_result(
            plan,
            started_at=started_at,
            status="fail",
            reason="frozen_dependency_audit_failed",
            commands=command_results,
            runtime_probe_result={"status": "not_run"},
        )
    version_audit = audit_python_runtime(
        target_python,
        expected_version=str(plan.get("python_version") or ""),
        runner=runner,
    )
    if version_audit["status"] != "pass":
        probe_result = {
            "status": "fail",
            "reason": "isolated_runtime_version_mismatch",
            "version": version_audit,
            "missing_modules": copy.deepcopy(
                _list(plan.get("required_runtime_modules"))
            ),
        }
    else:
        probe_result = runtime_probe(
            target_python,
            str(plan.get("python_version") or ""),
            [str(value) for value in _list(plan.get("required_runtime_modules"))],
        )
    ready = probe_result.get("status") == "pass"
    return _bootstrap_result(
        plan,
        started_at=started_at,
        status="pass" if ready else "fail",
        reason="isolated_runtime_ready" if ready else "runtime_dependency_probe_failed",
        commands=command_results,
        runtime_probe_result=probe_result,
    )


def validate_environment_bootstrap_plan(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if str(plan.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append("schema_version_must_be_4.0")
    execution_platform = str(plan.get("execution_platform") or "")
    if execution_platform not in {"darwin", "linux", "windows"}:
        errors.append("execution_platform_is_invalid")
    elif execution_platform != _host_execution_platform():
        errors.append("execution_platform_does_not_match_host")
    if str(plan.get("plan_sha256") or "") != environment_bootstrap_plan_fingerprint(
        plan
    ):
        errors.append("plan_sha256_mismatch")
    requirements = [str(value) for value in _list(plan.get("requirements"))]
    errors.extend(validate_bootstrap_requirements(requirements))
    runtime_variant = _dict(plan.get("runtime_variant"))
    if runtime_variant.get("status") != "pass":
        errors.append("runtime_variant_status_must_pass")
    if str(runtime_variant.get("case_id") or "") != str(plan.get("case_id") or ""):
        errors.append("runtime_variant_case_id_mismatch")
    variant_id = str(runtime_variant.get("variant_id") or "")
    requirements_sha256 = str(runtime_variant.get("requirements_sha256") or "")
    requirements_line_ending = str(
        runtime_variant.get("requirements_line_ending") or ""
    )
    line_separator = (
        "\n"
        if requirements_line_ending == "lf"
        else "\r\n"
        if requirements_line_ending == "crlf"
        else ""
    )
    observed_requirements_sha256 = hashlib.sha256(
        (
            (line_separator.join(requirements) + line_separator)
            if requirements and line_separator
            else ""
        ).encode("utf-8")
    ).hexdigest()
    if variant_id != "project_default" and (
        not line_separator
        or not re.fullmatch(r"[0-9a-f]{64}", requirements_sha256)
        or requirements_sha256 != observed_requirements_sha256
    ):
        errors.append("runtime_variant_requirements_sha256_mismatch")
    pip_requirements = [
        str(value) for value in _list(plan.get("pip_requirements"))
    ]
    manual_archives = _list(plan.get("manual_python_archives"))
    errors.extend(validate_manual_python_archives(manual_archives))
    expected_pip_requirements = _pip_requirements_for_archives(
        requirements,
        manual_archives,
    )
    if pip_requirements != expected_pip_requirements:
        errors.append("pip_requirements_mismatch")
    policy = _dict(plan.get("policy"))
    expected_dependency_source = _expected_dependency_source(manual_archives)
    if policy.get("dependency_source") != expected_dependency_source:
        errors.append("unsafe_policy:dependency_source")
    required_policy = {
        "exact_versions_required": True,
        "direct_urls_allowed": False,
        "vcs_requirements_allowed": False,
        "editable_install_allowed": False,
        "repository_setup_script_allowed": False,
        "repository_project_install_allowed": False,
        "manual_archive_install_allowed": bool(manual_archives),
        "hash_pinned_native_archive_install_allowed": any(
            _dict(value).get("artifact_kind") == "conda_python_binary"
            for value in manual_archives
        ),
        "manual_archive_setup_execution_allowed": False,
        "explicit_execution_authorization_required": True,
        "shared_base_runtime_mutation_allowed": False,
    }
    for key, expected in required_policy.items():
        if policy.get(key) is not expected:
            errors.append(f"unsafe_policy:{key}")
    base_root = Path(str(plan.get("base_runtime_root") or "")).resolve()
    base_python = Path(str(plan.get("base_python") or "")).resolve()
    if (
        not _within(base_python, base_root)
        or not base_python.is_file()
        or base_python.is_symlink()
    ):
        errors.append("base_python_is_missing_or_unsafe")
    isolated_root = Path(str(plan.get("isolated_runtime_root") or "")).resolve()
    environment_path = Path(str(plan.get("environment_path") or "")).resolve()
    target_python = Path(str(plan.get("target_python") or "")).resolve()
    site_packages_path = Path(str(plan.get("site_packages_path") or "")).resolve()
    environment_relative = str(plan.get("environment_relative_path") or "")
    if not _safe_relative_path(environment_relative):
        errors.append("environment_relative_path_is_unsafe")
    else:
        expected_environment = (
            isolated_root
            / Path(*PurePosixPath(environment_relative.replace("\\", "/")).parts)
        ).resolve()
        if expected_environment != environment_path:
            errors.append("environment_path_mismatch")
    if not _within(environment_path, isolated_root):
        errors.append("environment_path_outside_isolated_root")
    if not _within(target_python, environment_path):
        errors.append("target_python_outside_environment")
    if not _within(site_packages_path, environment_path):
        errors.append("site_packages_path_outside_environment")
    expected_target_python = (
        environment_path / "Scripts" / "python.exe"
        if execution_platform == "windows"
        else environment_path / "bin" / "python"
    ).resolve()
    if target_python != expected_target_python:
        errors.append("target_python_path_mismatch")
    python_version = str(plan.get("python_version") or "")
    major_minor = ".".join(python_version.split(".")[:2])
    expected_site_packages = (
        environment_path / "Lib" / "site-packages"
        if execution_platform == "windows"
        else environment_path / "lib" / f"python{major_minor}" / "site-packages"
    ).resolve()
    if site_packages_path != expected_site_packages:
        errors.append("site_packages_path_mismatch")
    commands = _dict(plan.get("commands"))
    expected_create = [
        str(plan.get("base_python") or ""),
        "-m",
        "venv",
    ]
    if execution_platform != "windows":
        expected_create.append("--copies")
    expected_create.append(str(environment_path))
    expected_install = [
        str(target_python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--no-deps",
        *pip_requirements,
    ]
    expected_check = [str(target_python), "-m", "pip", "check"]
    expected_audit = [str(target_python), "-m", "pip", "freeze", "--all"]
    if [str(value) for value in _list(commands.get("create_environment"))] != expected_create:
        errors.append("create_environment_command_mismatch")
    if [str(value) for value in _list(commands.get("install_dependencies"))] != expected_install:
        errors.append("install_dependencies_command_mismatch")
    if [str(value) for value in _list(commands.get("check_dependencies"))] != expected_check:
        errors.append("check_dependencies_command_mismatch")
    if [
        str(value)
        for value in _list(commands.get("audit_frozen_requirements"))
    ] != expected_audit:
        errors.append("audit_frozen_requirements_command_mismatch")
    return errors


def environment_bootstrap_plan_fingerprint(plan: dict[str, Any]) -> str:
    value = copy.deepcopy(plan)
    value.pop("generated_at", None)
    value.pop("plan_sha256", None)
    return canonical_json_sha256(value)


def environment_bootstrap_result_fingerprint(result: dict[str, Any]) -> str:
    value = copy.deepcopy(result)
    value.pop("result_sha256", None)
    return canonical_json_sha256(value)


def write_bootstrap_artifact(payload: dict[str, Any], output: str | Path) -> str:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        .replace("\r\n", "\n")
        .encode("utf-8")
    )
    return str(path)


def _bootstrap_result(
    plan: dict[str, Any],
    *,
    started_at: str,
    status: str,
    reason: str,
    commands: list[dict[str, Any]],
    runtime_probe_result: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "schema_version": SCHEMA_VERSION,
        "result_id": str(plan.get("plan_id") or "") + ":result",
        "plan_sha256": str(plan.get("plan_sha256") or ""),
        "project": str(plan.get("project") or ""),
        "case_id": str(plan.get("case_id") or ""),
        "runtime_variant": copy.deepcopy(_dict(plan.get("runtime_variant"))),
        "python_version": str(plan.get("python_version") or ""),
        "status": status,
        "reason": reason,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "environment_path": str(plan.get("environment_path") or ""),
        "target_python": str(plan.get("target_python") or ""),
        "commands": commands,
        "runtime_probe": runtime_probe_result,
        "policy": {
            "repository_setup_script_executed": False,
            "repository_project_installed": False,
            "manual_archive_setup_scripts_executed": False,
            "manual_archive_count": len(
                _list(plan.get("manual_python_archives"))
            ),
            "manual_binary_archive_count": sum(
                1
                for value in _list(plan.get("manual_python_archives"))
                if _dict(value).get("artifact_kind") == "conda_python_binary"
            ),
            "shared_base_runtime_mutated": False,
            "model_calls": 0,
        },
    }
    result["result_sha256"] = environment_bootstrap_result_fingerprint(result)
    return result


def _run_command(
    command: list[str],
    *,
    timeout: int,
    runner: Runner,
    environment: dict[str, str],
    stage: str,
) -> dict[str, Any]:
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=max(1, timeout),
            check=False,
            env=environment,
        )
        stdout = str(completed.stdout or "")[-12000:]
        stderr = str(completed.stderr or "")[-12000:]
        return {
            "stage": stage,
            "status": "pass" if completed.returncode == 0 else "fail",
            "reason": "command_completed" if completed.returncode == 0 else "nonzero_exit",
            "returncode": completed.returncode,
            "stdout_tail": stdout,
            "stderr_tail": stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "stage": stage,
            "status": "fail",
            "reason": "timeout",
            "returncode": None,
            "stdout_tail": str(exc.stdout or "")[-12000:],
            "stderr_tail": str(exc.stderr or "")[-12000:],
        }
    except OSError as exc:
        return {
            "stage": stage,
            "status": "fail",
            "reason": f"execution_error:{type(exc).__name__}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }


def _audit_frozen_requirements(
    *,
    plan: dict[str, Any],
    runner: Runner,
    environment: dict[str, str],
) -> dict[str, Any]:
    command = [
        str(value)
        for value in _list(
            _dict(plan.get("commands")).get("audit_frozen_requirements")
        )
    ]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return {
            "stage": "audit_frozen_requirements",
            "status": "fail",
            "reason": "timeout",
            "returncode": None,
        }
    except OSError as exc:
        return {
            "stage": "audit_frozen_requirements",
            "status": "fail",
            "reason": f"execution_error:{type(exc).__name__}",
            "returncode": None,
        }
    if completed.returncode != 0:
        return {
            "stage": "audit_frozen_requirements",
            "status": "fail",
            "reason": "pip_freeze_failed",
            "returncode": completed.returncode,
            "stderr_tail": str(completed.stderr or "")[-12000:],
        }
    expected: dict[str, str] = {}
    for raw_requirement in _list(plan.get("requirements")):
        requirement = Requirement(str(raw_requirement))
        specifier = list(requirement.specifier)[0]
        expected[str(canonicalize_name(requirement.name))] = specifier.version
    observed: dict[str, str] = {}
    ignored_lines: list[str] = []
    for raw_line in str(completed.stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            requirement = Requirement(line)
            specifiers = list(requirement.specifier)
        except InvalidRequirement:
            ignored_lines.append(line[:200])
            continue
        if (
            requirement.url
            or len(specifiers) != 1
            or specifiers[0].operator != "=="
        ):
            ignored_lines.append(line[:200])
            continue
        observed[str(canonicalize_name(requirement.name))] = specifiers[0].version
    missing = sorted(name for name in expected if name not in observed)
    mismatched = [
        {
            "package": name,
            "expected": expected[name],
            "observed": observed[name],
        }
        for name in sorted(expected)
        if name in observed and observed[name] != expected[name]
    ]
    passed = not missing and not mismatched
    return {
        "stage": "audit_frozen_requirements",
        "status": "pass" if passed else "fail",
        "reason": (
            "all_frozen_requirements_match"
            if passed
            else "frozen_requirement_mismatch"
        ),
        "returncode": completed.returncode,
        "expected_distribution_count": len(expected),
        "observed_distribution_count": len(observed),
        "missing_distributions": missing,
        "mismatched_distributions": mismatched,
        "ignored_freeze_lines": ignored_lines,
    }


def _install_manual_python_archive(
    *,
    archive: dict[str, Any],
    plan: dict[str, Any],
    proxy_url: str,
    fetcher: ArchiveFetcher,
) -> dict[str, Any]:
    archive_id = str(archive.get("archive_id") or "")
    stage = f"install_manual_archive:{archive_id}"
    expected_sha256 = str(archive.get("sha256") or "")
    expected_size = int(archive.get("size") or 0)
    isolated_root = Path(str(plan.get("isolated_runtime_root") or "")).resolve()
    cache_path = (
        isolated_root
        / ".bootstrap_artifacts"
        / f"{expected_sha256}{_archive_cache_suffix(archive)}"
    ).resolve()
    site_packages = Path(str(plan.get("site_packages_path") or "")).resolve()
    if not _within(cache_path, isolated_root) or not _within(
        site_packages,
        Path(str(plan.get("environment_path") or "")).resolve(),
    ):
        return _manual_archive_result(stage, archive, "unsafe_destination")
    try:
        if cache_path.exists():
            if not cache_path.is_file() or cache_path.is_symlink():
                return _manual_archive_result(
                    stage,
                    archive,
                    "artifact_cache_path_is_unsafe",
                )
            archive_bytes = cache_path.read_bytes()
            cache_status = "verified_existing_artifact"
        else:
            archive_bytes = fetcher(
                str(archive.get("url") or ""),
                proxy_url,
                expected_size,
            )
            cache_status = "downloaded_verified_artifact"
        if len(archive_bytes) != expected_size:
            return _manual_archive_result(
                stage,
                archive,
                "artifact_size_mismatch",
                observed_size=len(archive_bytes),
            )
        observed_sha256 = hashlib.sha256(archive_bytes).hexdigest()
        if observed_sha256 != expected_sha256:
            return _manual_archive_result(
                stage,
                archive,
                "artifact_sha256_mismatch",
                observed_sha256=observed_sha256,
                observed_size=len(archive_bytes),
            )
        writes, archive_errors = _validated_archive_writes(
            archive_bytes,
            archive=archive,
        )
        if archive_errors:
            return _manual_archive_result(
                stage,
                archive,
                archive_errors[0],
                archive_errors=archive_errors,
                observed_sha256=observed_sha256,
                observed_size=len(archive_bytes),
            )
        file_evidence: list[dict[str, Any]] = []
        pending_writes: list[tuple[Path, bytes, str]] = []
        for relative, payload in writes:
            destination = (site_packages / Path(*PurePosixPath(relative).parts)).resolve()
            if not _within(destination, site_packages):
                return _manual_archive_result(
                    stage,
                    archive,
                    "archive_destination_escaped_site_packages",
                )
            file_sha256 = hashlib.sha256(payload).hexdigest()
            disposition = "installed"
            if destination.exists():
                if (
                    not destination.is_file()
                    or destination.is_symlink()
                    or destination.read_bytes() != payload
                ):
                    return _manual_archive_result(
                        stage,
                        archive,
                        "archive_target_conflict",
                        conflict_path=relative,
                    )
                disposition = "already_present_identical"
            else:
                pending_writes.append((destination, payload, relative))
            file_evidence.append(
                {
                    "path": relative,
                    "sha256": file_sha256,
                    "size": len(payload),
                    "disposition": disposition,
                }
            )
        if not cache_path.exists():
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(archive_bytes)
        site_packages.mkdir(parents=True, exist_ok=True)
        for destination, payload, _ in pending_writes:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
        return {
            "stage": stage,
            "status": "pass",
            "reason": (
                "hash_pinned_conda_python_binary_installed"
                if archive.get("artifact_kind") == "conda_python_binary"
                else "hash_pinned_pure_python_archive_installed"
            ),
            "returncode": None,
            "archive_id": archive_id,
            "archive_sha256": observed_sha256,
            "archive_size": len(archive_bytes),
            "cache_status": cache_status,
            "installed_file_count": len(pending_writes),
            "verified_file_count": len(file_evidence),
            "files": file_evidence,
            "setup_script_executed": False,
        }
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        return _manual_archive_result(
            stage,
            archive,
            f"archive_processing_error:{type(exc).__name__}",
        )


def _validated_archive_writes(
    archive_bytes: bytes,
    *,
    archive: dict[str, Any],
) -> tuple[list[tuple[str, bytes]], list[str]]:
    if archive.get("archive_type") == "conda-tar-bz2":
        return _validated_conda_archive_writes(archive_bytes, archive=archive)
    return _validated_zip_archive_writes(archive_bytes, archive=archive)


def _validated_zip_archive_writes(
    archive_bytes: bytes,
    *,
    archive: dict[str, Any],
) -> tuple[list[tuple[str, bytes]], list[str]]:
    errors: list[str] = []
    selected: dict[str, bytes] = {}
    source_root = str(archive.get("source_root") or "")
    install_members = [
        str(value) for value in _list(archive.get("install_members"))
    ]
    matched_members = {member: False for member in install_members}
    seen_names: set[str] = set()
    expanded_size = 0
    source_prefix = source_root.rstrip("/") + "/"
    with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive_file:
        if len(archive_file.infolist()) > MAX_MANUAL_ARCHIVE_MEMBERS:
            return [], ["archive_member_count_limit_exceeded"]
        for info in archive_file.infolist():
            name = info.filename.replace("\\", "/")
            if not _safe_relative_path(name) or name in seen_names:
                errors.append("archive_member_path_is_unsafe_or_duplicated")
                continue
            seen_names.add(name)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                errors.append("archive_symbolic_link_is_forbidden")
            if info.flag_bits & 0x1:
                errors.append("archive_encrypted_member_is_forbidden")
            expanded_size += int(info.file_size)
            if expanded_size > MAX_MANUAL_ARCHIVE_EXPANDED_SIZE:
                errors.append("archive_expanded_size_limit_exceeded")
            if info.file_size > max(1, info.compress_size) * 1000:
                errors.append("archive_compression_ratio_limit_exceeded")
            if info.is_dir() or not name.startswith(source_prefix):
                continue
            relative = name[len(source_prefix) :]
            matched = False
            for member in install_members:
                normalized_member = member.rstrip("/")
                if relative == normalized_member or relative.startswith(
                    normalized_member + "/"
                ):
                    matched_members[member] = True
                    matched = True
            if not matched:
                continue
            if not _safe_relative_path(relative) or not relative.endswith(
                (".py", ".pyi")
            ):
                errors.append("archive_selected_member_is_not_pure_python")
                continue
            if relative in selected:
                errors.append("archive_install_destination_is_duplicated")
                continue
            selected[relative] = archive_file.read(info)
    for member, matched in matched_members.items():
        if not matched:
            errors.append(f"archive_install_member_not_found:{member}")
    if not selected:
        errors.append("archive_contains_no_selected_python_files")
    return sorted(selected.items()), sorted(set(errors))


def _validated_conda_archive_writes(
    archive_bytes: bytes,
    *,
    archive: dict[str, Any],
) -> tuple[list[tuple[str, bytes]], list[str]]:
    errors: list[str] = []
    selected: dict[str, bytes] = {}
    source_root = str(archive.get("source_root") or "")
    source_prefix = source_root.rstrip("/") + "/"
    install_members = [
        str(value) for value in _list(archive.get("install_members"))
    ]
    excluded_members = [
        str(value).rstrip("/")
        for value in _list(archive.get("exclude_members"))
    ]
    matched_members = {member: False for member in install_members}
    seen_names: set[str] = set()
    expanded_size = 0
    metadata_payloads: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:bz2") as archive_file:
        members = archive_file.getmembers()
        if len(members) > MAX_MANUAL_ARCHIVE_MEMBERS:
            return [], ["archive_member_count_limit_exceeded"]
        for info in members:
            name = info.name.replace("\\", "/")
            if not _safe_relative_path(name) or name in seen_names:
                errors.append("archive_member_path_is_unsafe_or_duplicated")
                continue
            seen_names.add(name)
            if info.issym() or info.islnk():
                errors.append("archive_link_member_is_forbidden")
                continue
            if not (info.isfile() or info.isdir()):
                errors.append("archive_special_member_is_forbidden")
                continue
            expanded_size += int(info.size)
            if expanded_size > MAX_MANUAL_ARCHIVE_EXPANDED_SIZE:
                errors.append("archive_expanded_size_limit_exceeded")
            metadata_paths = {
                "info/index.json",
                source_prefix + str(archive.get("dist_info_dir") or "") + "/WHEEL",
                source_prefix
                + str(archive.get("dist_info_dir") or "")
                + "/METADATA",
            }
            if info.isfile() and name in metadata_paths:
                extracted = archive_file.extractfile(info)
                if extracted is None:
                    errors.append("archive_metadata_cannot_be_read")
                else:
                    metadata_payloads[name] = extracted.read()
            if info.isdir() or not name.startswith(source_prefix):
                continue
            relative = name[len(source_prefix) :]
            if any(
                relative == excluded or relative.startswith(excluded + "/")
                for excluded in excluded_members
            ):
                continue
            matched = False
            for member in install_members:
                normalized_member = member.rstrip("/")
                if relative == normalized_member or relative.startswith(
                    normalized_member + "/"
                ):
                    matched_members[member] = True
                    matched = True
            if not matched:
                continue
            if not _safe_relative_path(relative) or not _allowed_conda_install_file(
                relative,
                archive=archive,
            ):
                errors.append("archive_selected_member_type_is_not_allowed")
                continue
            if relative in selected:
                errors.append("archive_install_destination_is_duplicated")
                continue
            extracted = archive_file.extractfile(info)
            if extracted is None:
                errors.append("archive_selected_member_cannot_be_read")
            else:
                selected[relative] = extracted.read()
    errors.extend(_validate_conda_archive_metadata(metadata_payloads, archive=archive))
    for member, matched in matched_members.items():
        if not matched:
            errors.append(f"archive_install_member_not_found:{member}")
    if not selected:
        errors.append("archive_contains_no_selected_python_files")
    if len(archive_bytes) and expanded_size > len(archive_bytes) * 1000:
        errors.append("archive_compression_ratio_limit_exceeded")
    return sorted(selected.items()), sorted(set(errors))


def _validate_conda_archive_metadata(
    metadata_payloads: dict[str, bytes],
    *,
    archive: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    index_payload = metadata_payloads.get("info/index.json")
    if index_payload is None:
        errors.append("conda_index_metadata_is_missing")
    else:
        try:
            index = _dict(json.loads(index_payload.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            errors.append("conda_index_metadata_is_invalid")
        else:
            expected = {
                "name": str(archive.get("package") or ""),
                "version": str(archive.get("version") or ""),
                "build": str(archive.get("conda_build") or ""),
                "subdir": str(archive.get("conda_subdir") or ""),
            }
            for key, value in expected.items():
                if str(index.get(key) or "") != value:
                    errors.append(f"conda_index_{key}_mismatch")
    metadata_root = (
        str(archive.get("source_root") or "").rstrip("/")
        + "/"
        + str(archive.get("dist_info_dir") or "")
    )
    wheel_path = metadata_root + "/WHEEL"
    wheel_payload = metadata_payloads.get(wheel_path)
    if wheel_payload is None:
        errors.append("conda_wheel_metadata_is_missing")
    else:
        try:
            wheel_text = wheel_payload.decode("utf-8")
        except UnicodeDecodeError:
            errors.append("conda_wheel_metadata_is_invalid")
        else:
            expected_tag = "Tag: " + str(archive.get("wheel_tag") or "")
            if expected_tag not in wheel_text.splitlines():
                errors.append("conda_wheel_tag_mismatch")
            if "Root-Is-Purelib: false" not in wheel_text.splitlines():
                errors.append("conda_wheel_must_be_platform_binary")
    distribution_payload = metadata_payloads.get(metadata_root + "/METADATA")
    if distribution_payload is None:
        errors.append("conda_distribution_metadata_is_missing")
    else:
        try:
            distribution = BytesParser().parsebytes(distribution_payload)
        except (TypeError, ValueError):
            errors.append("conda_distribution_metadata_is_invalid")
        else:
            if str(distribution.get("Name") or "") != str(
                archive.get("package") or ""
            ):
                errors.append("conda_distribution_name_mismatch")
            if str(distribution.get("Version") or "") != str(
                archive.get("version") or ""
            ):
                errors.append("conda_distribution_version_mismatch")
    return errors


def _allowed_conda_install_file(relative: str, *, archive: dict[str, Any]) -> bool:
    pure_path = PurePosixPath(relative)
    dist_info_marker = str(archive.get("dist_info_dir") or "")
    if (
        relative.endswith((".py", ".pyi"))
        and pure_path.parts[0] != dist_info_marker
    ):
        return True
    if any(
        relative.endswith(str(value))
        for value in _list(archive.get("allowed_native_suffixes"))
    ) and any(
        relative == str(value).rstrip("/")
        or relative.startswith(str(value).rstrip("/") + "/")
        for value in _list(archive.get("native_module_roots"))
    ):
        return True
    return (
        len(pure_path.parts) == 2
        and pure_path.parts[0] == dist_info_marker
        and pure_path.name in CONDA_DIST_INFO_FILES
    )


def _archive_cache_suffix(archive: dict[str, Any]) -> str:
    return ".tar.bz2" if archive.get("archive_type") == "conda-tar-bz2" else ".zip"


def _pip_requirements_for_archives(
    requirements: list[str],
    archives: list[Any],
) -> list[str]:
    replacements = {
        str(canonicalize_name(str(_dict(value).get("package") or "")))
        for value in archives
        if _dict(value).get("replaces_pip_requirement") is True
    }
    return [
        requirement
        for requirement in requirements
        if str(canonicalize_name(Requirement(requirement).name)) not in replacements
    ]


def _expected_dependency_source(archives: list[Any]) -> str:
    kinds = {
        str(_dict(value).get("artifact_kind") or "pure_python")
        for value in archives
    }
    if "conda_python_binary" in kinds:
        return "pypi_binary_wheels_and_hash_pinned_conda_python_binaries"
    if archives:
        return "pypi_binary_wheels_and_hash_pinned_pure_python_archives"
    return "pypi_binary_wheels_only"


def _manual_archive_result(
    stage: str,
    archive: dict[str, Any],
    reason: str,
    **evidence: Any,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": "fail",
        "reason": reason,
        "returncode": None,
        "archive_id": str(archive.get("archive_id") or ""),
        "setup_script_executed": False,
        **evidence,
    }


def _fetch_manual_archive(url: str, proxy_url: str, expected_size: int) -> bytes:
    parsed = urlparse(url)
    allowed_hosts = PURE_PYTHON_ARCHIVE_HOSTS | CONDA_BINARY_ARCHIVE_HOSTS
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise ValueError("Manual archive URL is not allowed.")
    handlers: list[Any] = []
    if proxy_url:
        proxy_overrides = _validated_proxy_overrides(proxy_url)
        handlers.append(
            urllib.request.ProxyHandler(
                {
                    "http": proxy_overrides["HTTP_PROXY"],
                    "https": proxy_overrides["HTTPS_PROXY"],
                }
            )
        )
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "code-intelligence-agent-v4-bootstrap/1.0"},
    )
    with opener.open(request, timeout=60) as response:
        final_url = urlparse(response.geturl())
        if (
            final_url.scheme != "https"
            or final_url.hostname not in allowed_hosts
        ):
            raise ValueError("Manual archive redirect is not allowed.")
        payload = response.read(expected_size + 1)
    if len(payload) > expected_size:
        raise ValueError("Manual archive exceeded the frozen size.")
    return payload


def _validated_proxy_overrides(proxy_url: str) -> dict[str, str]:
    if not proxy_url:
        return {}
    parsed = urlparse(proxy_url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
        or not parsed.port
    ):
        raise ValueError("Only an unauthenticated loopback HTTP(S) proxy is allowed.")
    return {"HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url}


def _host_execution_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    return sys.platform


def _resolve_within(root: Path, relative: str) -> Path:
    candidate = (
        root / Path(*PurePosixPath(relative.replace("\\", "/")).parts)
    ).resolve()
    if not _within(candidate, root):
        raise ValueError(f"Path escapes configured root: {relative}")
    return candidate


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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or execute an authorized project-isolated V4 runtime."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("profiles")
    plan.add_argument("project")
    plan.add_argument("python_version")
    plan.add_argument("output")
    plan.add_argument("--base-runtime-root", required=True)
    plan.add_argument("--isolated-runtime-root", required=True)
    plan.add_argument("--case-id", default="")
    run = subparsers.add_parser("run")
    run.add_argument("plan")
    run.add_argument("output")
    run.add_argument("--authorize-dependency-install", action="store_true")
    run.add_argument("--proxy", default="")
    run.add_argument("--create-timeout", type=int, default=180)
    run.add_argument("--install-timeout", type=int, default=900)
    run.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    if args.command == "plan":
        plan = build_environment_bootstrap_plan(
            profiles=load_json_object(args.profiles),
            project=args.project,
            python_version=args.python_version,
            base_runtime_root=args.base_runtime_root,
            isolated_runtime_root=args.isolated_runtime_root,
            case_id=args.case_id,
        )
        write_bootstrap_artifact(plan, args.output)
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return
    plan = load_json_object(args.plan)
    result = execute_environment_bootstrap(
        plan,
        authorize_dependency_install=args.authorize_dependency_install,
        proxy_url=args.proxy,
        create_timeout=args.create_timeout,
        install_timeout=args.install_timeout,
    )
    write_bootstrap_artifact(result, args.output)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.require_pass and result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
