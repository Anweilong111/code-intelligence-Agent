from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from code_intelligence_agent.evaluation.v3_experiment_protocol import (
    canonical_json_sha256,
)
from code_intelligence_agent.evaluation.v3_real_bug_benchmark import (
    SCHEMA_VERSION,
    load_json_object,
)


Runner = Callable[..., subprocess.CompletedProcess]
PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]+$")
RUNTIME_INFO_SCRIPT = (
    "import json, platform, sys; "
    "print(json.dumps({"
    "'python_version': platform.python_version(), "
    "'implementation': sys.implementation.name, "
    "'architecture': platform.machine(), "
    "'platform': sys.platform"
    "}, sort_keys=True))"
)


def collect_environment_profiles(
    config: dict[str, Any],
    catalog: dict[str, Any],
    *,
    root: str | Path,
    runner: Runner = subprocess.run,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root_path = Path(root).resolve()
    errors: list[str] = []
    catalog_cases = {
        str(case.get("case_id") or ""): _dict(case)
        for case in _list(catalog.get("cases"))
    }
    assigned_cases: dict[str, str] = {}
    profile_ids: set[str] = set()
    profiles: list[dict[str, Any]] = []

    if str(config.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append("config_schema_version_must_be_3.0")
    for source_value in _list(config.get("profiles")):
        source = _dict(source_value)
        profile_id = str(source.get("profile_id") or "")
        prefix = f"profile:{profile_id or '<missing>'}"
        if not PROFILE_ID_PATTERN.fullmatch(profile_id):
            errors.append(f"{prefix}:invalid_profile_id")
            continue
        if profile_id in profile_ids:
            errors.append(f"{prefix}:duplicate_profile_id")
            continue
        profile_ids.add(profile_id)
        runtime_relative_dir = str(source.get("runtime_relative_dir") or "")
        if not _safe_relative_path(runtime_relative_dir):
            errors.append(f"{prefix}:unsafe_runtime_relative_dir")
            continue
        runtime_dir = (
            root_path
            / Path(*PurePosixPath(runtime_relative_dir.replace("\\", "/")).parts)
        ).resolve()
        if not _within(runtime_dir, root_path):
            errors.append(f"{prefix}:runtime_path_escape")
            continue
        executable = runtime_dir / ("python.exe" if _is_windows_profile(config) else "bin/python")
        expected_version = str(source.get("expected_python_version") or "")
        case_ids = [str(item) for item in _list(source.get("case_ids"))]
        binding_errors = _validate_case_bindings(
            profile_id,
            expected_version,
            case_ids,
            catalog_cases,
            assigned_cases,
        )
        errors.extend(binding_errors)
        captured, capture_errors = _capture_profile(
            executable,
            expected_version=expected_version,
            runner=runner,
        )
        errors.extend(f"{prefix}:{item}" for item in capture_errors)
        if capture_errors:
            continue
        profile = {
            "profile_id": profile_id,
            "python_version": captured["runtime"]["python_version"],
            "implementation": captured["runtime"]["implementation"],
            "architecture": captured["runtime"]["architecture"],
            "platform": captured["runtime"]["platform"],
            "case_ids": sorted(case_ids),
            "case_count": len(case_ids),
            "packages": captured["packages"],
            "package_count": len(captured["packages"]),
            "capture_command": "python -m pip list --format=json",
            "runtime_material_committed": False,
        }
        profile["profile_sha256"] = canonical_json_sha256(profile)
        profiles.append(profile)

    missing_cases = sorted(set(catalog_cases) - set(assigned_cases))
    if missing_cases:
        errors.append("unassigned_catalog_cases:" + ",".join(missing_cases))
    unknown_cases = sorted(set(assigned_cases) - set(catalog_cases))
    if unknown_cases:
        errors.append("unknown_assigned_cases:" + ",".join(unknown_cases))
    profiles.sort(key=lambda item: str(item.get("profile_id") or ""))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not errors else "fail",
        "platform_scope": str(config.get("platform_scope") or ""),
        "catalog_sha256": str(catalog.get("catalog_sha256") or ""),
        "profile_count": len(profiles),
        "bound_case_count": len(assigned_cases),
        "runtime_material_committed": False,
        "profiles": profiles,
    }
    manifest["manifest_sha256"] = canonical_json_sha256(manifest)
    audit = {
        "schema_version": SCHEMA_VERSION,
        "status": manifest["status"],
        "error_count": len(errors),
        "errors": sorted(set(errors)),
        "profile_count": len(profiles),
        "bound_case_count": len(assigned_cases),
        "catalog_case_count": len(catalog_cases),
        "manifest_sha256": manifest["manifest_sha256"],
    }
    return manifest, audit


def render_environment_profiles_markdown(
    manifest: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    lines = [
        "# V3 Environment Profiles",
        "",
        f"- Status: `{audit.get('status')}`",
        f"- Profiles: `{audit.get('profile_count')}`",
        f"- Bound cases: `{audit.get('bound_case_count')}` / `{audit.get('catalog_case_count')}`",
        f"- Manifest SHA-256: `{audit.get('manifest_sha256')}`",
        "- Runtime directories are ignored; only exact package/version snapshots and fingerprints are committed.",
        "",
        "| Profile | Python | Platform | Packages | Cases | Fingerprint |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for profile_value in _list(manifest.get("profiles")):
        profile = _dict(profile_value)
        lines.append(
            "| "
            f"{profile.get('profile_id')} | "
            f"{profile.get('python_version')} | "
            f"{profile.get('platform')}-{profile.get('architecture')} | "
            f"{profile.get('package_count')} | "
            f"{profile.get('case_count')} | "
            f"`{str(profile.get('profile_sha256') or '')[:12]}` |"
        )
    lines.append("")
    return "\n".join(lines)


def write_environment_profile_artifacts(
    manifest: dict[str, Any],
    audit: dict[str, Any],
    output_prefix: str | Path,
) -> dict[str, str]:
    prefix = Path(output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = prefix.with_suffix(".json")
    audit_path = prefix.with_name(prefix.name + "_audit").with_suffix(".json")
    markdown_path = prefix.with_suffix(".md")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(
        render_environment_profiles_markdown(manifest, audit), encoding="utf-8"
    )
    return {
        "manifest_json": str(manifest_path),
        "audit_json": str(audit_path),
        "markdown": str(markdown_path),
    }


def _capture_profile(
    executable: Path,
    *,
    expected_version: str,
    runner: Runner,
) -> tuple[dict[str, Any], list[str]]:
    if not executable.is_file():
        return {}, ["python_executable_missing"]
    runtime_result = runner(
        [str(executable), "-c", RUNTIME_INFO_SCRIPT],
        cwd=str(executable.parent),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if runtime_result.returncode != 0:
        return {}, ["runtime_probe_failed"]
    try:
        runtime = json.loads(runtime_result.stdout.strip())
    except json.JSONDecodeError:
        return {}, ["runtime_probe_invalid_json"]
    if not isinstance(runtime, dict):
        return {}, ["runtime_probe_must_be_object"]
    errors: list[str] = []
    if str(runtime.get("python_version") or "") != expected_version:
        errors.append("python_version_mismatch")
    package_result = runner(
        [
            str(executable),
            "-m",
            "pip",
            "list",
            "--format=json",
            "--disable-pip-version-check",
        ],
        cwd=str(executable.parent),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if package_result.returncode != 0:
        errors.append("pip_list_failed")
        return {}, errors
    try:
        package_values = json.loads(package_result.stdout)
    except json.JSONDecodeError:
        return {}, errors + ["pip_list_invalid_json"]
    if not isinstance(package_values, list) or not package_values:
        return {}, errors + ["pip_list_must_be_nonempty_array"]
    packages: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in package_values:
        package = _dict(value)
        name = str(package.get("name") or "").strip()
        version = str(package.get("version") or "").strip()
        normalized_name = re.sub(r"[-_.]+", "-", name).lower()
        if not normalized_name or not version:
            errors.append("package_name_and_version_required")
            continue
        if normalized_name in seen:
            errors.append(f"duplicate_package:{normalized_name}")
            continue
        seen.add(normalized_name)
        packages.append({"name": normalized_name, "version": version})
    packages.sort(key=lambda item: item["name"])
    return {"runtime": runtime, "packages": packages}, errors


def _validate_case_bindings(
    profile_id: str,
    expected_version: str,
    case_ids: list[str],
    catalog_cases: dict[str, dict[str, Any]],
    assigned_cases: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    if not expected_version:
        errors.append(f"profile:{profile_id}:expected_python_version_required")
    if not case_ids:
        errors.append(f"profile:{profile_id}:case_ids_required")
    for case_id in case_ids:
        if case_id in assigned_cases:
            errors.append(f"duplicate_case_binding:{case_id}")
            continue
        assigned_cases[case_id] = profile_id
        case = catalog_cases.get(case_id)
        if case is None:
            continue
        if str(case.get("environment_profile_id") or "") != profile_id:
            errors.append(f"case_profile_id_mismatch:{case_id}")
        if str(case.get("python_version") or "") != expected_version:
            errors.append(f"case_python_version_mismatch:{case_id}")
    return errors


def _is_windows_profile(config: dict[str, Any]) -> bool:
    return str(config.get("platform_scope") or "").lower().startswith("win")


def _safe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("//") or re.match(r"^[A-Za-z]:", normalized):
        return False
    path = PurePosixPath(normalized)
    return not path.is_absolute() and ".." not in path.parts


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture exact V3 historical Python environment profiles."
    )
    parser.add_argument("config", help="Environment profile source JSON.")
    parser.add_argument("catalog", help="Accepted real-bug catalog JSON.")
    parser.add_argument("output_prefix", help="Output prefix for profile artifacts.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    config = load_json_object(args.config)
    catalog = load_json_object(args.catalog)
    manifest, audit = collect_environment_profiles(
        config,
        catalog,
        root=args.root,
    )
    write_environment_profile_artifacts(manifest, audit, args.output_prefix)
    print(render_environment_profiles_markdown(manifest, audit))
    if args.require_pass and audit["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
