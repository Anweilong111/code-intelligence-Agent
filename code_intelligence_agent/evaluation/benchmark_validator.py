from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    location: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationReport:
    target_path: str
    target_type: str
    issues: list[ValidationIssue]

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_path": self.target_path,
            "target_type": self.target_type,
            "is_valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
        }


class BenchmarkValidator:
    def validate_template(self, template_path: str | Path) -> ValidationReport:
        path = Path(template_path)
        data = _load_json(path)
        issues: list[ValidationIssue] = []
        cases = data.get("cases")
        if not isinstance(cases, list) or not cases:
            issues.append(_error("cases", "Template must contain a non-empty cases list."))
            cases = []
        _validate_unique_names(cases, issues)
        for index, case in enumerate(cases):
            _validate_template_case(case, f"cases[{index}]", issues)
        return ValidationReport(str(path), "template", issues)

    def validate_manifest(
        self,
        manifest_path: str | Path,
        require_existing_repo: bool = True,
    ) -> ValidationReport:
        path = Path(manifest_path)
        data = _load_json(path)
        issues: list[ValidationIssue] = []
        cases = data.get("cases")
        if not isinstance(cases, list) or not cases:
            issues.append(_error("cases", "Manifest must contain a non-empty cases list."))
            cases = []
        _validate_unique_names(cases, issues)
        for index, case in enumerate(cases):
            _validate_manifest_case(
                case,
                f"cases[{index}]",
                path.parent,
                issues,
                require_existing_repo=require_existing_repo,
            )
        return ValidationReport(str(path), "manifest", issues)


def _validate_template_case(
    case: Any,
    location: str,
    issues: list[ValidationIssue],
) -> None:
    if not isinstance(case, dict):
        issues.append(_error(location, "Case must be an object."))
        return
    _require_string(case, "name", location, issues)
    _require_safe_relative_path(case, "repo_path", location, issues)
    source_targets = _validate_sources(case.get("sources", []), location, issues)
    _validate_overlay_files(case.get("files", []), location, issues)
    _validate_mutations(case.get("mutations", []), source_targets, location, issues)
    benchmark = case.get("benchmark", {})
    if not isinstance(benchmark, dict):
        issues.append(_error(f"{location}.benchmark", "Benchmark must be an object."))
        return
    _validate_ground_truth(benchmark, f"{location}.benchmark", issues)


def _validate_manifest_case(
    case: Any,
    location: str,
    manifest_dir: Path,
    issues: list[ValidationIssue],
    require_existing_repo: bool,
) -> None:
    if not isinstance(case, dict):
        issues.append(_error(location, "Case must be an object."))
        return
    _require_string(case, "name", location, issues)
    repo_path = case.get("repo_path")
    if not isinstance(repo_path, str) or not repo_path:
        issues.append(_error(f"{location}.repo_path", "repo_path must be a non-empty string."))
    elif _is_unsafe_relative(repo_path):
        issues.append(_error(f"{location}.repo_path", "repo_path must not escape the manifest directory."))
    elif require_existing_repo:
        resolved = Path(repo_path)
        if not resolved.is_absolute():
            resolved = (manifest_dir / resolved).resolve()
        if not resolved.exists():
            issues.append(_error(f"{location}.repo_path", f"Repository path does not exist: {resolved}"))
    _validate_ground_truth(case, location, issues)
    _require_list(case, "test_args", location, issues, allow_missing=True)
    metadata = case.get("metadata", {})
    if metadata and not isinstance(metadata, dict):
        issues.append(_error(f"{location}.metadata", "metadata must be an object."))
    if isinstance(metadata, dict) and metadata.get("source") == "github_raw_mutation":
        mutations = metadata.get("materialized_mutations")
        if not isinstance(mutations, list) or not mutations:
            issues.append(
                _warning(
                    f"{location}.metadata.materialized_mutations",
                    "github_raw_mutation cases should record materialized mutations.",
                )
            )


def _validate_sources(
    sources: Any,
    location: str,
    issues: list[ValidationIssue],
) -> set[str]:
    if sources is None:
        return set()
    if not isinstance(sources, list):
        issues.append(_error(f"{location}.sources", "sources must be a list."))
        return set()
    targets: set[str] = set()
    for index, source in enumerate(sources):
        item_location = f"{location}.sources[{index}]"
        if not isinstance(source, dict):
            issues.append(_error(item_location, "Source must be an object."))
            continue
        target = _require_safe_relative_path(source, "target_path", item_location, issues)
        if target:
            targets.add(target)
        has_raw_url = bool(source.get("raw_url"))
        github_fields = ["owner", "repo", "ref", "source_path"]
        has_github_fields = all(bool(source.get(field)) for field in github_fields)
        if not has_raw_url and not has_github_fields:
            issues.append(
                _error(
                    item_location,
                    "Source must define raw_url or owner/repo/ref/source_path.",
                )
            )
        sha256 = source.get("sha256")
        if sha256 is not None and not _is_sha256(sha256):
            issues.append(_error(f"{item_location}.sha256", "sha256 must be a 64-character hex digest."))
    return targets


def _validate_overlay_files(
    files: Any,
    location: str,
    issues: list[ValidationIssue],
) -> None:
    if files is None:
        return
    if not isinstance(files, list):
        issues.append(_error(f"{location}.files", "files must be a list."))
        return
    for index, file in enumerate(files):
        item_location = f"{location}.files[{index}]"
        if not isinstance(file, dict):
            issues.append(_error(item_location, "File overlay must be an object."))
            continue
        _require_safe_relative_path(file, "target_path", item_location, issues)
        if not isinstance(file.get("content"), str):
            issues.append(_error(f"{item_location}.content", "content must be a string."))


def _validate_mutations(
    mutations: Any,
    source_targets: set[str],
    location: str,
    issues: list[ValidationIssue],
) -> None:
    if mutations is None:
        return
    if not isinstance(mutations, list):
        issues.append(_error(f"{location}.mutations", "mutations must be a list."))
        return
    for index, mutation in enumerate(mutations):
        item_location = f"{location}.mutations[{index}]"
        if not isinstance(mutation, dict):
            issues.append(_error(item_location, "Mutation must be an object."))
            continue
        target = _require_safe_relative_path(mutation, "target_path", item_location, issues)
        if target and source_targets and target not in source_targets:
            issues.append(
                _error(
                    f"{item_location}.target_path",
                    "Mutation target must match a fetched source target_path.",
                )
            )
        if not isinstance(mutation.get("find"), str) or not mutation.get("find"):
            issues.append(_error(f"{item_location}.find", "find must be a non-empty string."))
        if not isinstance(mutation.get("replace"), str):
            issues.append(_error(f"{item_location}.replace", "replace must be a string."))
        count = mutation.get("count", 1)
        if not isinstance(count, int) or count < 1:
            issues.append(_error(f"{item_location}.count", "count must be a positive integer."))


def _validate_ground_truth(
    data: dict[str, Any],
    location: str,
    issues: list[ValidationIssue],
) -> None:
    buggy_functions = _require_list(data, "buggy_functions", location, issues)
    expected_rules = _require_list(data, "expected_rule_ids", location, issues)
    _require_list(data, "failing_tests", location, issues, allow_missing=True)
    _require_list(data, "passed_tests", location, issues, allow_missing=True)
    if buggy_functions == []:
        issues.append(_warning(f"{location}.buggy_functions", "No buggy functions declared."))
    if expected_rules == []:
        issues.append(_warning(f"{location}.expected_rule_ids", "No expected rule ids declared."))


def _validate_unique_names(cases: list[Any], issues: list[ValidationIssue]) -> None:
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        name = case.get("name")
        if not isinstance(name, str):
            continue
        if name in seen:
            issues.append(_error(f"cases[{index}].name", f"Duplicate case name: {name}"))
        seen.add(name)


def _require_string(
    data: dict[str, Any],
    field: str,
    location: str,
    issues: list[ValidationIssue],
) -> str | None:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        issues.append(_error(f"{location}.{field}", f"{field} must be a non-empty string."))
        return None
    return value


def _require_safe_relative_path(
    data: dict[str, Any],
    field: str,
    location: str,
    issues: list[ValidationIssue],
) -> str | None:
    value = _require_string(data, field, location, issues)
    if value is None:
        return None
    if _is_unsafe_relative(value):
        issues.append(_error(f"{location}.{field}", f"{field} must be a safe relative path."))
        return None
    return Path(value).as_posix()


def _require_list(
    data: dict[str, Any],
    field: str,
    location: str,
    issues: list[ValidationIssue],
    allow_missing: bool = False,
) -> list[Any] | None:
    if field not in data and allow_missing:
        return None
    value = data.get(field)
    if not isinstance(value, list):
        issues.append(_error(f"{location}.{field}", f"{field} must be a list."))
        return None
    return value


def _is_unsafe_relative(value: str) -> bool:
    path = Path(value)
    return path.is_absolute() or ".." in path.parts


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", value))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _error(location: str, message: str) -> ValidationIssue:
    return ValidationIssue("error", location, message)


def _warning(location: str, message: str) -> ValidationIssue:
    return ValidationIssue("warning", location, message)


def render_validation_text(report: ValidationReport) -> str:
    lines = [
        f"{report.target_type}: {report.target_path}",
        f"valid: {report.is_valid}",
        f"errors: {len(report.errors)}",
        f"warnings: {len(report.warnings)}",
    ]
    for issue in report.issues:
        lines.append(f"- {issue.severity.upper()} {issue.location}: {issue.message}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate CIA benchmark templates or manifests.")
    parser.add_argument("path", help="Template or manifest JSON path")
    parser.add_argument(
        "--target",
        choices=["template", "manifest"],
        default="manifest",
        help="Validate a benchmark template or generated manifest.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Validation report format.",
    )
    parser.add_argument(
        "--allow-missing-repo",
        action="store_true",
        help="For manifest validation, do not require repo_path directories to exist.",
    )
    args = parser.parse_args()

    validator = BenchmarkValidator()
    if args.target == "template":
        report = validator.validate_template(args.path)
    else:
        report = validator.validate_manifest(
            args.path,
            require_existing_repo=not args.allow_missing_repo,
        )
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_validation_text(report))
    raise SystemExit(0 if report.is_valid else 1)


if __name__ == "__main__":
    main()
