from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = "3.0"
CASE_STATUSES = {"candidate", "accepted", "rejected"}
SPLITS = {"development", "validation", "test"}
SUPPORTED_TEST_MODULES = {"nose", "pytest", "unittest"}
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ASSIGNMENT_PATTERN = re.compile(
    r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"\s*$'
)
DIFF_FILE_PATTERN = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
HUNK_PATTERN = re.compile(r"^@@ .+? @@\s*(.*)$")
FUNCTION_CONTEXT_PATTERNS = (
    re.compile(r"^(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)"),
)
REQUIRED_DIFFICULTY_TAGS = {
    "static_negative",
    "cross_function",
    "data_flow",
    "separated_failure_site",
    "high_similarity_candidates",
    "multi_file",
}


def load_json_object(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def import_bugsinpy_selection(
    source_root: str | Path,
    selection: dict[str, Any],
) -> dict[str, Any]:
    root = Path(source_root).resolve()
    source = _dict(selection.get("source"))
    cases: list[dict[str, Any]] = []
    import_errors: list[dict[str, str]] = []
    projects = [_dict(item) for item in _list(selection.get("projects"))]
    for project in projects:
        project_name = str(project.get("name") or "")
        project_root = root / "projects" / project_name
        try:
            project_info = parse_assignment_file(project_root / "project.info")
        except (OSError, ValueError) as exc:
            import_errors.append(
                {"project": project_name, "case": "", "reason": str(exc)}
            )
            continue
        for selected_case in [_dict(item) for item in _list(project.get("cases"))]:
            bug_id = str(selected_case.get("bug_id") or "")
            try:
                cases.append(
                    _import_bugsinpy_case(
                        root=root,
                        project=project,
                        project_info=project_info,
                        selected_case=selected_case,
                        bug_id=bug_id,
                    )
                )
            except (OSError, ValueError) as exc:
                import_errors.append(
                    {
                        "project": project_name,
                        "case": bug_id,
                        "reason": str(exc),
                    }
                )
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "catalog_id": str(selection.get("catalog_id") or "v3-real-python-bugs"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "adapter": "bugsinpy_metadata_v1",
            "repository_url": str(source.get("repository_url") or ""),
            "commit_sha": str(source.get("commit_sha") or ""),
            "license_status": str(source.get("license_status") or ""),
            "shell_scripts_executed": False,
        },
        "case_count": len(cases),
        "import_error_count": len(import_errors),
        "import_errors": import_errors,
        "cases": sorted(cases, key=lambda item: str(item.get("case_id") or "")),
    }
    catalog["catalog_sha256"] = catalog_sha256(catalog)
    return catalog


def parse_assignment_file(path: str | Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = ASSIGNMENT_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError(
                f"Unsupported assignment syntax at {Path(path).name}:{line_number}"
            )
        key, value = match.groups()
        if key in result:
            raise ValueError(f"Duplicate assignment key: {key}")
        result[key] = value
    return result


def parse_test_commands(path: str | Path) -> list[list[str]]:
    commands: list[list[str]] = []
    for line_number, raw_line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if _contains_shell_control(line):
            raise ValueError(f"Shell control token is forbidden at line {line_number}")
        try:
            parts = shlex.split(line, posix=True)
        except ValueError as exc:
            raise ValueError(f"Invalid test command at line {line_number}: {exc}") from exc
        if not parts:
            continue
        executable = parts[0].lower()
        if executable in {"python", "python3", "python.exe"}:
            normalized = ["{python}", *parts[1:]]
        elif executable in {"pytest", "pytest.exe"}:
            normalized = ["{python}", "-m", "pytest", *parts[1:]]
        else:
            raise ValueError(f"Unsupported test executable: {parts[0]}")
        _validate_normalized_test_command(normalized)
        commands.append(normalized)
    if not commands:
        raise ValueError("At least one targeted test command is required.")
    return commands


def inspect_setup_script(path: str | Path) -> dict[str, Any]:
    script_path = Path(path)
    if not script_path.is_file():
        return {
            "present": False,
            "source_path": "",
            "risk_level": "none",
            "actions": [],
            "executed": False,
        }
    actions: list[dict[str, Any]] = []
    max_risk = 0
    for line_number, raw_line in enumerate(
        script_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if _contains_shell_control(line):
            actions.append(
                {
                    "line": line_number,
                    "kind": "unsupported_shell_control",
                    "risk": "high",
                    "command_preview": line[:200],
                }
            )
            max_risk = max(max_risk, 3)
            continue
        try:
            parts = shlex.split(line, posix=True)
        except ValueError:
            parts = []
        kind, risk = _classify_setup_action(parts)
        actions.append(
            {
                "line": line_number,
                "kind": kind,
                "risk": risk,
                "argv": parts,
            }
        )
        max_risk = max(max_risk, {"none": 0, "low": 1, "medium": 2, "high": 3}[risk])
    return {
        "present": True,
        "source_path": script_path.name,
        "risk_level": ("none", "low", "medium", "high")[max_risk],
        "actions": actions,
        "executed": False,
    }


def parse_patch_ground_truth(path: str | Path) -> dict[str, Any]:
    patch_path = Path(path)
    text = patch_path.read_text(encoding="utf-8")
    files: list[str] = []
    functions: list[str] = []
    current_file = ""
    for line in text.splitlines():
        file_match = DIFF_FILE_PATTERN.match(line)
        if file_match:
            current_file = file_match.group(2)
            if current_file not in files:
                files.append(current_file)
            continue
        hunk_match = HUNK_PATTERN.match(line)
        if not hunk_match:
            continue
        context = hunk_match.group(1).strip()
        symbol = _function_symbol(context)
        if symbol:
            qualified = f"{current_file}:{symbol}" if current_file else symbol
            if qualified not in functions:
                functions.append(qualified)
    test_files = [item for item in files if _is_test_path(item)]
    source_files = [item for item in files if item not in test_files]
    return {
        "patch_sha256": _sha256_file(patch_path),
        "changed_files": files,
        "source_files": source_files,
        "test_files": test_files,
        "functions": functions,
        "source_file_count": len(source_files),
        "test_file_count": len(test_files),
    }


def validate_real_bug_catalog(
    catalog: dict[str, Any],
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if str(catalog.get("schema_version") or "") != SCHEMA_VERSION:
        errors.append("schema_version_must_be_3.0")
    source = _dict(catalog.get("source"))
    if not COMMIT_PATTERN.fullmatch(str(source.get("commit_sha") or "")):
        errors.append("source.commit_sha_must_be_full_sha")
    if source.get("shell_scripts_executed") is not False:
        errors.append("source_shell_scripts_must_not_be_executed_by_importer")
    cases = [_dict(item) for item in _list(catalog.get("cases"))]
    if _int(catalog.get("case_count"), -1) != len(cases):
        errors.append("case_count_mismatch")
    if _int(catalog.get("import_error_count"), -1) != len(
        _list(catalog.get("import_errors"))
    ):
        errors.append("import_error_count_mismatch")
    if require_complete and len(cases) < 20:
        errors.append("complete_catalog_requires_at_least_20_cases")
    if require_complete and _list(catalog.get("import_errors")):
        errors.append("complete_catalog_cannot_have_import_errors")

    case_ids: set[str] = set()
    case_revisions: set[tuple[str, str, str]] = set()
    bug_commit_cases: dict[tuple[str, str], list[str]] = {}
    repository_splits: dict[str, set[str]] = {}
    covered_tags: set[str] = set()
    status_counts = {status: 0 for status in sorted(CASE_STATUSES)}
    for index, case in enumerate(cases):
        prefix = f"case[{index}]"
        case_id = str(case.get("case_id") or "")
        if not case_id:
            errors.append(f"{prefix}.case_id_is_required")
        elif case_id in case_ids:
            errors.append(f"duplicate_case_id:{case_id}")
        case_ids.add(case_id)
        status = str(case.get("status") or "")
        if status not in CASE_STATUSES:
            errors.append(f"{prefix}.status_is_invalid")
        else:
            status_counts[status] += 1
        split = str(case.get("benchmark_split") or "")
        if split not in SPLITS:
            errors.append(f"{prefix}.benchmark_split_is_invalid")
        repository = _dict(case.get("repository"))
        repository_url = str(repository.get("url") or "")
        if not repository_url.startswith("https://github.com/"):
            errors.append(f"{prefix}.repository.url_is_invalid")
        if not str(repository.get("license_spdx") or ""):
            errors.append(f"{prefix}.repository.license_spdx_is_required")
        repository_splits.setdefault(repository_url, set()).add(split)
        bug_sha = str(case.get("bug_commit_sha") or "")
        fix_sha = str(case.get("fix_commit_sha") or "")
        if not COMMIT_PATTERN.fullmatch(bug_sha):
            errors.append(f"{prefix}.bug_commit_sha_must_be_full_sha")
        if not COMMIT_PATTERN.fullmatch(fix_sha):
            errors.append(f"{prefix}.fix_commit_sha_must_be_full_sha")
        revision = (repository_url, bug_sha, fix_sha)
        if revision in case_revisions:
            errors.append(
                f"duplicate_repository_bug_fix_pair:{repository_url}@{bug_sha}:{fix_sha}"
            )
        case_revisions.add(revision)
        bug_commit_cases.setdefault((repository_url, bug_sha), []).append(case_id)
        if not str(case.get("python_version") or ""):
            errors.append(f"{prefix}.python_version_is_required")
        if not str(case.get("environment_profile_id") or ""):
            errors.append(f"{prefix}.environment_profile_id_is_required")
        issue_url = str(_dict(case.get("provenance")).get("issue_or_pr_url") or "")
        if status == "accepted" and not issue_url.startswith("https://github.com/"):
            errors.append(f"{prefix}.accepted_case_requires_issue_or_pr_url")
        elif not issue_url:
            warnings.append(f"{prefix}.issue_or_pr_url_pending")
        overlay_paths = [str(item) for item in _list(case.get("test_overlay_paths"))]
        if not overlay_paths or any(not _safe_repo_relative_path(item) for item in overlay_paths):
            errors.append(f"{prefix}.test_overlay_paths_are_invalid")
        commands = [_list(item) for item in _list(case.get("targeted_test_commands"))]
        if not commands:
            errors.append(f"{prefix}.targeted_test_commands_are_required")
        for command in commands:
            try:
                _validate_normalized_test_command([str(item) for item in command])
            except ValueError as exc:
                errors.append(f"{prefix}.unsafe_test_command:{exc}")
        regression_command = [str(item) for item in _list(case.get("regression_command"))]
        try:
            _validate_normalized_test_command(regression_command)
        except ValueError as exc:
            errors.append(f"{prefix}.unsafe_regression_command:{exc}")
        regression_provenance = _dict(case.get("regression_provenance"))
        platform_exclusions = [
            _dict(item)
            for item in _list(regression_provenance.get("platform_exclusions"))
        ]
        for exclusion in platform_exclusions:
            excluded_test = str(exclusion.get("test") or "")
            if not excluded_test or not str(exclusion.get("reason") or ""):
                errors.append(f"{prefix}.platform_exclusion_requires_test_and_reason")
                continue
            if not _command_declares_test_exclusion(regression_command, excluded_test):
                errors.append(f"{prefix}.platform_exclusion_not_in_regression_command")
            if _test_reference_overlaps_case_target(excluded_test, case):
                errors.append(f"{prefix}.platform_exclusion_overlaps_target_or_ground_truth")
        for excluded_file in _list(
            regression_provenance.get("excluded_online_or_external_tool_files")
        ):
            if _test_reference_overlaps_case_target(str(excluded_file), case):
                errors.append(
                    f"{prefix}.external_test_exclusion_overlaps_target_or_ground_truth"
                )
        for prepared_value in _list(case.get("preparation_files")):
            prepared = _dict(prepared_value)
            prepared_path = str(prepared.get("path") or "")
            prepared_content = prepared.get("content")
            if not _safe_repo_relative_path(prepared_path):
                errors.append(f"{prefix}.preparation_file_path_is_invalid")
            if not isinstance(prepared_content, str) or len(
                prepared_content.encode("utf-8")
            ) > 4096:
                errors.append(f"{prefix}.preparation_file_content_is_invalid")
            if not str(prepared.get("reason") or ""):
                errors.append(f"{prefix}.preparation_file_reason_is_required")
        test_environment = _dict(case.get("test_environment"))
        for key in ("pythonpath_entries", "optional_pythonpath_entries"):
            for entry in _list(test_environment.get(key)):
                if not _safe_repo_relative_path(str(entry)):
                    errors.append(
                        f"{prefix}.test_environment_{key}_is_invalid"
                    )
        for tool in _list(test_environment.get("required_tools")):
            if str(tool) not in {"ls"}:
                errors.append(f"{prefix}.test_environment_tool_is_unsupported")
        ground_truth = _dict(case.get("ground_truth"))
        if not _list(ground_truth.get("source_files")):
            errors.append(f"{prefix}.ground_truth.source_files_are_required")
        if not re.fullmatch(r"[0-9a-f]{64}", str(ground_truth.get("patch_sha256") or "")):
            errors.append(f"{prefix}.ground_truth.patch_sha256_is_invalid")
        tags = {str(item) for item in _list(case.get("difficulty_tags"))}
        covered_tags.update(tags)
        evidence = _dict(case.get("difficulty_tag_evidence"))
        for tag in tags:
            if not str(evidence.get(tag) or ""):
                errors.append(f"{prefix}.difficulty_tag_without_evidence:{tag}")
        reproduction = _dict(case.get("reproduction"))
        if status == "accepted":
            expected = {
                "bug_targeted": "fail",
                "fix_targeted": "pass",
                "fix_full_regression": "pass",
            }
            for key, expected_status in expected.items():
                observed = str(_dict(reproduction.get(key)).get("status") or "")
                if observed != expected_status:
                    errors.append(f"{prefix}.accepted_reproduction_{key}_must_{expected_status}")
        if status == "rejected" and not str(case.get("rejection_reason") or ""):
            errors.append(f"{prefix}.rejected_case_requires_reason")
        if status == "rejected" and not str(
            _dict(case.get("rejection_evidence")).get("summary") or ""
        ):
            errors.append(f"{prefix}.rejected_case_requires_evidence_summary")

    leaked_repositories = {
        repository: sorted(splits)
        for repository, splits in repository_splits.items()
        if len(splits) > 1
    }
    errors.extend(
        f"repository_split_leakage:{repository}:{','.join(splits)}"
        for repository, splits in leaked_repositories.items()
    )
    reused_bug_commits = {
        f"{repository}@{bug_sha}": sorted(case_names)
        for (repository, bug_sha), case_names in bug_commit_cases.items()
        if len(case_names) > 1
    }
    warnings.extend(
        f"shared_bug_commit_requires_grouped_analysis:{key}"
        for key in sorted(reused_bug_commits)
    )
    missing_tags = sorted(REQUIRED_DIFFICULTY_TAGS - covered_tags)
    if require_complete and missing_tags:
        errors.append("missing_required_difficulty_tags:" + ",".join(missing_tags))
    if require_complete and status_counts["accepted"] < 20:
        errors.append("complete_catalog_requires_20_accepted_cases")

    fingerprint_source = json.loads(json.dumps(catalog))
    fingerprint_source.pop("catalog_sha256", None)
    actual_hash = catalog_sha256(fingerprint_source)
    expected_hash = str(catalog.get("catalog_sha256") or "")
    if expected_hash and expected_hash != actual_hash:
        errors.append("catalog_sha256_mismatch")
    if not expected_hash:
        warnings.append("catalog_sha256_not_pinned")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if not errors else "fail",
        "case_count": len(cases),
        "status_counts": status_counts,
        "repository_count": len(repository_splits),
        "split_counts": {
            split: sum(str(case.get("benchmark_split") or "") == split for case in cases)
            for split in sorted(SPLITS)
        },
        "covered_difficulty_tags": sorted(covered_tags),
        "missing_required_difficulty_tags": missing_tags,
        "repository_split_leakage": leaked_repositories,
        "reused_bug_commits": reused_bug_commits,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "catalog_sha256": actual_hash,
    }


def catalog_sha256(catalog: dict[str, Any]) -> str:
    value = json.loads(json.dumps(catalog))
    value.pop("catalog_sha256", None)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def render_real_bug_catalog_markdown(
    catalog: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    lines = [
        "# V3 Real Python Bug Catalog",
        "",
        f"- Status: `{audit.get('status')}`",
        f"- Cases: `{audit.get('case_count')}`",
        f"- Repositories: `{audit.get('repository_count')}`",
        f"- Candidate/accepted/rejected: `{_dict(audit.get('status_counts')).get('candidate', 0)}` / `{_dict(audit.get('status_counts')).get('accepted', 0)}` / `{_dict(audit.get('status_counts')).get('rejected', 0)}`",
        f"- Catalog SHA-256: `{audit.get('catalog_sha256')}`",
        f"- Import errors: `{catalog.get('import_error_count')}`",
        "",
        "| Case | Split | Repository | Python | Status | Targeted commands | Ground truth files |",
        "| --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for case_value in _list(catalog.get("cases")):
        case = _dict(case_value)
        lines.append(
            "| "
            f"{_cell(case.get('case_id'))} | "
            f"{_cell(case.get('benchmark_split'))} | "
            f"{_cell(_dict(case.get('repository')).get('owner_repo'))} | "
            f"{_cell(case.get('python_version'))} | "
            f"{_cell(case.get('status'))} | "
            f"{len(_list(case.get('targeted_test_commands')))} | "
            f"{len(_list(_dict(case.get('ground_truth')).get('source_files')))} |"
        )
    lines.extend(
        [
            "",
            "## Safety Boundary",
            "",
            "The importer parses assignment files, test commands, setup scripts, and patches as data. It does not execute benchmark shell scripts or setup commands. Candidate cases become accepted only after independent bug/fix reproduction artifacts exist.",
            "",
        ]
    )
    return "\n".join(lines)


def write_real_bug_catalog_artifacts(
    catalog: dict[str, Any],
    output_prefix: str | Path,
    *,
    require_complete: bool = False,
) -> dict[str, str]:
    audit = validate_real_bug_catalog(catalog, require_complete=require_complete)
    prefix = Path(output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    catalog_path = prefix.with_suffix(".json")
    audit_path = prefix.with_name(prefix.name + "_audit").with_suffix(".json")
    markdown_path = prefix.with_suffix(".md")
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(
        render_real_bug_catalog_markdown(catalog, audit), encoding="utf-8"
    )
    return {
        "catalog_json": str(catalog_path),
        "audit_json": str(audit_path),
        "catalog_markdown": str(markdown_path),
    }


def _import_bugsinpy_case(
    *,
    root: Path,
    project: dict[str, Any],
    project_info: dict[str, str],
    selected_case: dict[str, Any],
    bug_id: str,
) -> dict[str, Any]:
    if not bug_id.isdigit() or int(bug_id) <= 0:
        raise ValueError(f"Invalid bug id: {bug_id}")
    project_name = str(project.get("name") or "")
    case_root = root / "projects" / project_name / "bugs" / bug_id
    bug_info = parse_assignment_file(case_root / "bug.info")
    targeted_commands = parse_test_commands(case_root / "run_test.sh")
    setup_observation = inspect_setup_script(case_root / "setup.sh")
    ground_truth = parse_patch_ground_truth(case_root / "bug_patch.txt")
    repository_url = str(project_info.get("github_url") or "").rstrip("/")
    owner_repo = repository_url.removeprefix("https://github.com/")
    bug_sha = str(bug_info.get("buggy_commit_id") or "").lower()
    fix_sha = str(bug_info.get("fixed_commit_id") or "").lower()
    test_paths = [
        item.strip()
        for item in str(bug_info.get("test_file") or "").split(";")
        if item.strip()
    ]
    overlay_additions = [
        _dict(item)
        for item in _list(selected_case.get("test_overlay_additions"))
    ]
    for addition in overlay_additions:
        path = str(addition.get("path") or "")
        reason = str(addition.get("reason") or "")
        if not _safe_repo_relative_path(path):
            raise ValueError(f"Unsafe additional test overlay path: {path}")
        if not reason:
            raise ValueError(f"Additional test overlay requires reason: {path}")
        if path not in test_paths:
            test_paths.append(path)
    for path in test_paths:
        if not _safe_repo_relative_path(path):
            raise ValueError(f"Unsafe test overlay path: {path}")
    selected_regression = _list(selected_case.get("regression_command"))
    regression_command = [
        str(item)
        for item in (
            selected_regression
            if selected_regression
            else _list(project.get("regression_command"))
        )
    ]
    _validate_normalized_test_command(regression_command)
    difficulty_tags = [
        str(item) for item in _list(selected_case.get("difficulty_tags"))
    ]
    tag_evidence = {
        str(key): str(value)
        for key, value in _dict(selected_case.get("difficulty_tag_evidence")).items()
    }
    metadata_relative = (
        Path("projects") / project_name / "bugs" / bug_id / "bug.info"
    ).as_posix()
    patch_relative = (
        Path("projects") / project_name / "bugs" / bug_id / "bug_patch.txt"
    ).as_posix()
    license_config = _dict(project.get("license"))
    license_url = str(license_config.get("url") or "")
    license_url_template = str(license_config.get("url_template") or "")
    if license_url_template:
        license_url = license_url_template.format(bug_commit_sha=bug_sha)
    issue_or_pr_url = str(selected_case.get("issue_or_pr_url") or "")
    if not issue_or_pr_url:
        issue_or_pr_url = str(
            _dict(project.get("issue_or_pr_urls")).get(bug_id) or ""
        )
    status = str(selected_case.get("status") or "candidate")
    if status not in CASE_STATUSES:
        raise ValueError(f"Invalid selected case status: {status}")
    rejection_reason = str(selected_case.get("rejection_reason") or "")
    if status == "rejected" and not rejection_reason:
        raise ValueError("Rejected selected case requires rejection_reason")
    rejection_evidence = {
        str(key): value
        for key, value in _dict(selected_case.get("rejection_evidence")).items()
    }
    if status == "rejected" and not str(rejection_evidence.get("summary") or ""):
        raise ValueError("Rejected selected case requires rejection_evidence.summary")
    return {
        "case_id": f"bugsinpy-{project_name.lower()}-{bug_id}",
        "status": status,
        "rejection_reason": rejection_reason,
        "rejection_evidence": rejection_evidence,
        "benchmark_split": str(project.get("benchmark_split") or ""),
        "repository": {
            "url": repository_url,
            "owner_repo": owner_repo,
            "license_spdx": str(license_config.get("spdx") or ""),
            "license_url": license_url,
        },
        "bug_commit_sha": bug_sha,
        "fix_commit_sha": fix_sha,
        "python_version": str(bug_info.get("python_version") or ""),
        "environment_profile_id": str(
            selected_case.get("environment_profile_id")
            or project.get("environment_profile_id")
            or ""
        ),
        "test_overlay_paths": test_paths,
        "test_overlay_provenance": {
            "benchmark_declared_paths": [
                item.strip()
                for item in str(bug_info.get("test_file") or "").split(";")
                if item.strip()
            ],
            "additional_support_files": overlay_additions,
        },
        "targeted_test_commands": targeted_commands,
        "regression_command": regression_command,
        "regression_provenance": {
            **_dict(project.get("regression_provenance")),
            **_dict(selected_case.get("regression_provenance")),
        },
        "preparation_files": [
            _dict(item)
            for item in (
                _list(project.get("preparation_files"))
                + _list(selected_case.get("preparation_files"))
            )
        ],
        "test_environment": {
            **_dict(project.get("test_environment")),
            **_dict(selected_case.get("test_environment")),
        },
        "setup_observation": setup_observation,
        "ground_truth": {
            **ground_truth,
            "benchmark_patch_path": patch_relative,
            "visible_to_model": False,
        },
        "difficulty_tags": difficulty_tags,
        "difficulty_tag_evidence": tag_evidence,
        "provenance": {
            "benchmark": "BugsInPy",
            "benchmark_case": f"{project_name}:{bug_id}",
            "benchmark_metadata_path": metadata_relative,
            "issue_or_pr_url": issue_or_pr_url,
            "bug_commit_url": f"{repository_url}/commit/{bug_sha}",
            "fix_commit_url": f"{repository_url}/commit/{fix_sha}",
        },
        "reproduction": {
            "bug_targeted": {"status": "pending", "artifact": ""},
            "fix_targeted": {"status": "pending", "artifact": ""},
            "fix_full_regression": {"status": "pending", "artifact": ""},
            "test_overlay_policy": "copy_listed_test_files_from_fix_commit_to_bug_commit",
        },
    }


def _validate_normalized_test_command(command: list[str]) -> None:
    if len(command) < 3 or command[0] != "{python}" or command[1] != "-m":
        raise ValueError("command_must_start_with_{python}_-m")
    if command[2] not in SUPPORTED_TEST_MODULES:
        raise ValueError(f"unsupported_test_module:{command[2]}")
    for part in command:
        if not isinstance(part, str) or not part or _contains_shell_control(part):
            raise ValueError("empty_or_shell_control_argument")
        if "\x00" in part or "\n" in part or "\r" in part:
            raise ValueError("control_character_in_argument")


def _contains_shell_control(value: str) -> bool:
    return any(token in value for token in ("&&", "||", ";", "|", ">", "<", "`", "$("))


def _classify_setup_action(parts: list[str]) -> tuple[str, str]:
    if not parts:
        return "unparsed", "high"
    executable = parts[0].lower()
    if executable == "touch" and len(parts) == 2 and _safe_repo_relative_path(parts[1]):
        return "file_touch", "low"
    if executable in {"pip", "pip3"} and len(parts) >= 3 and parts[1] == "install":
        return "dependency_install", "medium"
    if executable in {"python", "python3"} and len(parts) >= 3:
        if parts[1] == "-m" and parts[2] == "pip":
            return "dependency_install", "medium"
        if parts[1] == "setup.py" and parts[2] in {"install", "develop"}:
            return "local_build_install", "high"
    return "unsupported", "high"


def _function_symbol(context: str) -> str:
    for pattern in FUNCTION_CONTEXT_PATTERNS:
        match = pattern.match(context)
        if match:
            return match.group(1)
    return ""


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    parts = normalized.split("/")
    name = parts[-1]
    return (
        any(part in {"test", "tests", "testing"} for part in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _safe_repo_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if (
        not value
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        return False
    pure = PurePosixPath(normalized)
    return not pure.is_absolute() and ".." not in pure.parts


def _command_declares_test_exclusion(
    command: list[str],
    excluded_test: str,
) -> bool:
    excluded = _canonical_test_reference(excluded_test)
    excluded_leaf = excluded.rsplit(".", 1)[-1]
    for argument in command:
        if argument.startswith("--deselect="):
            if _canonical_test_reference(argument.split("=", 1)[1]) == excluded:
                return True
        if argument.startswith("--exclude="):
            candidate = _canonical_test_reference(argument.split("=", 1)[1])
            if candidate in {excluded, excluded_leaf}:
                return True
    return False


def _test_reference_overlaps_case_target(
    test_reference: str,
    case: dict[str, Any],
) -> bool:
    excluded = _canonical_test_reference(test_reference)
    if not excluded:
        return False
    references = [
        str(item)
        for item in _list(_dict(case.get("ground_truth")).get("functions"))
    ]
    for command_value in _list(case.get("targeted_test_commands")):
        references.extend(str(item) for item in _list(command_value)[3:])
    has_node_selector = "::" in test_reference or bool(
        re.search(r"\.py[:.]", test_reference, flags=re.IGNORECASE)
    )
    if not has_node_selector:
        references.extend(str(item) for item in _list(case.get("test_overlay_paths")))
        references.extend(
            str(item)
            for item in _list(_dict(case.get("ground_truth")).get("test_files"))
        )
    for reference in references:
        candidate = _canonical_test_reference(reference)
        if not candidate:
            continue
        if (
            candidate == excluded
            or candidate.startswith(excluded + ".")
            or excluded.startswith(candidate + ".")
        ):
            return True
    return False


def _canonical_test_reference(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if normalized.startswith("--") and "=" in normalized:
        normalized = normalized.split("=", 1)[1]
    normalized = re.sub(r"\.py(?=::|$)", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace("/", ".").replace("::", ".").replace(":", ".")
    return normalized.strip(".").lower()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


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
        description="Import and audit a fixed BugsInPy selection without executing shell scripts."
    )
    parser.add_argument("source_root", help="Prepared BugsInPy checkout root.")
    parser.add_argument("selection", help="V3 source-selection JSON.")
    parser.add_argument("output_prefix", help="Output prefix for catalog artifacts.")
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--require-pass", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    selection = load_json_object(args.selection)
    catalog = import_bugsinpy_selection(args.source_root, selection)
    audit = validate_real_bug_catalog(catalog, require_complete=args.require_complete)
    write_real_bug_catalog_artifacts(
        catalog,
        args.output_prefix,
        require_complete=args.require_complete,
    )
    print(render_real_bug_catalog_markdown(catalog, audit))
    if args.require_pass and audit["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
