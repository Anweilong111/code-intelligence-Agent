from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


AUXILIARY_PACKAGE_ROOTS = {
    "bench",
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


def build_github_repository_profile(
    discovery_payload: dict[str, Any],
    import_report: dict[str, Any],
    *,
    sampled_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = [_dict(row) for row in _list(import_report.get("rows"))]
    imported_sources = [
        _dict(source) for source in _list(import_report.get("source_entries"))
    ]
    raw_paths = [
        _clean_path(str(row.get("source_path") or _dict(row.get("source")).get("source_path") or ""))
        for row in rows
    ]
    raw_paths = [path for path in raw_paths if path]
    imported_paths = [
        _clean_path(str(source.get("source_path") or source.get("target_path") or ""))
        for source in imported_sources
    ]
    imported_paths = [path for path in imported_paths if path]
    sampled = sampled_sources or []
    sampled_directories = {_source_directory(source) for source in sampled}
    extension_counts = Counter(_extension(path) for path in raw_paths)
    skip_reason_counts = Counter(
        str(row.get("reason", ""))
        for row in rows
        if str(row.get("status", "")) == "skipped"
    )
    directory_counts = Counter(_directory(path) for path in imported_paths)
    test_source_paths = [path for path in imported_paths if _is_test_path(path)]
    test_content_profile = _test_content_profile(
        imported_sources,
        test_source_paths,
    )
    config_files = _project_config_files(raw_paths)
    package_roots = _package_roots(imported_paths)
    src_layout_packages = _src_layout_packages(imported_paths)
    recommended_target_prefix = _recommended_target_prefix(
        package_roots=package_roots,
        src_layout_packages=src_layout_packages,
    )
    test_frameworks = _test_frameworks(
        raw_paths=raw_paths,
        test_source_paths=test_source_paths,
        config_files=config_files,
        pytest_test_source_paths=[
            str(item)
            for item in _list(test_content_profile.get("pytest_test_source_paths"))
        ],
        unittest_test_source_paths=[
            str(item)
            for item in _list(test_content_profile.get("unittest_test_source_paths"))
        ],
    )
    framework_profile = _framework_profile(
        raw_paths=raw_paths,
        imported_paths=imported_paths,
        config_files=config_files,
    )
    dependency_profile = _dependency_manager_profile(config_files)
    test_command_candidates = _test_command_candidates(
        test_frameworks=test_frameworks,
        test_source_paths=test_source_paths,
        config_files=config_files,
        pytest_test_source_paths=[
            str(item)
            for item in _list(test_content_profile.get("pytest_test_source_paths"))
        ],
        unittest_test_source_paths=[
            str(item)
            for item in _list(test_content_profile.get("unittest_test_source_paths"))
        ],
    )
    recommended_test_command = _recommended_test_command(
        test_frameworks=test_frameworks,
        test_source_paths=test_source_paths,
        config_files=config_files,
        test_command_candidates=test_command_candidates,
    )
    discovery_items = _discovery_item_count(discovery_payload)
    python_source_ratio = _ratio(
        _int(import_report.get("source_count", 0)),
        max(1, discovery_items),
    )
    repository_doctor = _repository_doctor(
        discovery_item_count=discovery_items,
        imported_source_count=_int(import_report.get("source_count", 0)),
        python_source_ratio=python_source_ratio,
        test_source_count=len(test_source_paths),
        project_config_count=len(config_files),
        recommended_test_command=recommended_test_command,
    )
    return {
        "discovery_item_count": discovery_items,
        "input_item_count": _int(import_report.get("input_count", 0)),
        "imported_source_count": _int(import_report.get("source_count", 0)),
        "skipped_source_count": _int(import_report.get("skipped_count", 0)),
        "python_source_ratio": python_source_ratio,
        "test_source_count": len(test_source_paths),
        "test_source_paths": test_source_paths[:20],
        "package_init_count": sum(
            1 for path in imported_paths if PurePosixPath(path).name == "__init__.py"
        ),
        "package_roots": package_roots,
        "src_layout_packages": src_layout_packages,
        "source_directory_count": len(directory_counts),
        "sampled_source_directory_count": len(sampled_directories),
        "extension_counts": dict(sorted(extension_counts.items())),
        "skip_reason_counts": dict(sorted(skip_reason_counts.items())),
        "top_source_directories": dict(directory_counts.most_common(10)),
        "sampled_targets": [
            str(source.get("target_path") or source.get("source_path") or "")
            for source in sampled
        ],
        "project_config_files": config_files,
        "project_config_count": len(config_files),
        "test_framework_signals": test_frameworks,
        "framework_signals": [
            str(item) for item in _list(framework_profile.get("frameworks"))
        ],
        "framework_profile": framework_profile,
        "dependency_manager_profile": dependency_profile,
        "dependency_tool_signals": [
            str(item) for item in _list(dependency_profile.get("tool_signals"))
        ],
        "dependency_file_count": _int(dependency_profile.get("dependency_file_count", 0)),
        "packaging_file_count": _int(dependency_profile.get("packaging_file_count", 0)),
        "test_command_candidate_count": len(test_command_candidates),
        "test_command_candidates": test_command_candidates,
        "recommended_test_command": recommended_test_command,
        "test_content_profile": test_content_profile,
        "repository_doctor": repository_doctor,
        "doctor_status": str(repository_doctor.get("status") or ""),
        "doctor_blocker": str(repository_doctor.get("blocker") or ""),
        "doctor_next_action": str(repository_doctor.get("next_action") or ""),
        "doctor_score": _float(repository_doctor.get("score", 0.0)),
        "recommended_target_prefix": recommended_target_prefix,
        "layout_hints": _layout_hints(
            imported_paths=imported_paths,
            config_files=config_files,
            test_source_paths=test_source_paths,
            package_roots=package_roots,
            src_layout_packages=src_layout_packages,
            recommended_target_prefix=recommended_target_prefix,
        ),
    }


def render_github_repository_profile_markdown(profile: dict[str, Any]) -> str:
    lines = [
        "# GitHub Repository Profile",
        "",
        f"- Discovery Items: {_int(profile.get('discovery_item_count', 0))}",
        f"- Imported Sources: {_int(profile.get('imported_source_count', 0))}",
        f"- Python Source Ratio: {_float(profile.get('python_source_ratio', 0.0)):.4f}",
        f"- Test Sources: {_int(profile.get('test_source_count', 0))}",
        f"- Project Config Files: {_int(profile.get('project_config_count', 0))}",
        (
            "- Test Framework Signals: "
            f"{', '.join(str(item) for item in _list(profile.get('test_framework_signals'))) or 'none'}"
        ),
        (
            "- Framework Signals: "
            f"{', '.join(str(item) for item in _list(profile.get('framework_signals'))) or 'none'}"
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
            "- Repository Doctor Status: "
            f"{_markdown_cell(profile.get('doctor_status') or 'unknown')}"
        ),
        (
            "- Repository Doctor Blocker: "
            f"{_markdown_cell(profile.get('doctor_blocker') or 'none')}"
        ),
        (
            "- Repository Doctor Score: "
            f"{_float(profile.get('doctor_score', 0.0)):.2f}"
        ),
        (
            "- Repository Doctor Next Action: "
            f"{_markdown_cell(profile.get('doctor_next_action') or 'none')}"
        ),
        "",
        "## Repository Doctor Checks",
        "",
        "| Check | Status | Expected | Actual | Message |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in _list(_dict(profile.get("repository_doctor")).get("checks")):
        row = _dict(check)
        lines.append(
            "| "
            f"{_markdown_cell(row.get('name', ''))} | "
            f"{_markdown_cell(row.get('status', ''))} | "
            f"{_markdown_cell(row.get('expected', ''))} | "
            f"{_markdown_cell(row.get('actual', ''))} | "
            f"{_markdown_cell(row.get('message', ''))} |"
        )
    if not _list(_dict(profile.get("repository_doctor")).get("checks")):
        lines.append("| none | skipped | none | none | none |")
    lines.extend(
        [
        "",
        "## Test Command Candidates",
        "",
        "| Rank | Command | Runner | Confidence | Reason | Evidence |",
        "| ---: | --- | --- | ---: | --- | --- |",
        ]
    )
    for candidate in _list(profile.get("test_command_candidates")):
        row = _dict(candidate)
        lines.append(
            "| "
            f"{_int(row.get('rank', 0))} | "
            f"`{_markdown_cell(row.get('command', ''))}` | "
            f"{_markdown_cell(row.get('runner', ''))} | "
            f"{_float(row.get('confidence', 0.0)):.2f} | "
            f"{_markdown_cell(row.get('reason', ''))} | "
            f"{_markdown_cell(', '.join(str(item) for item in _list(row.get('evidence'))))} |"
        )
    if not _list(profile.get("test_command_candidates")):
        lines.append("| 0 | none | none | 0.00 | none | none |")
    framework_profile = _dict(profile.get("framework_profile"))
    lines.extend(
        [
            "",
            "## Framework Profile",
            "",
            (
                "- Frameworks: "
                + (
                    ", ".join(
                        str(item) for item in _list(framework_profile.get("frameworks"))
                    )
                    or "none"
                )
            ),
            (
                "- Suggested Environment Variables: "
                + (
                    ", ".join(
                        f"{key}={value}"
                        for key, value in _dict(
                            framework_profile.get("environment_variables")
                        ).items()
                    )
                    or "none"
                )
            ),
            "",
            "| Framework | Signal | Evidence |",
            "| --- | --- | --- |",
        ]
    )
    for signal in _list(framework_profile.get("signals")):
        row = _dict(signal)
        lines.append(
            "| "
            f"{_markdown_cell(row.get('framework', ''))} | "
            f"{_markdown_cell(row.get('signal', ''))} | "
            f"{_markdown_cell(', '.join(str(item) for item in _list(row.get('evidence'))))} |"
        )
    if not _list(framework_profile.get("signals")):
        lines.append("| none | none | none |")
    dependency_profile = _dict(profile.get("dependency_manager_profile"))
    lines.extend(
        [
            "",
            "## Dependency And Packaging Profile",
            "",
            f"- Status: `{_markdown_cell(dependency_profile.get('status') or 'none')}`",
            f"- Reason: `{_markdown_cell(dependency_profile.get('reason') or 'none')}`",
            (
                "- Tool Signals: "
                + (
                    ", ".join(
                        str(item) for item in _list(dependency_profile.get("tool_signals"))
                    )
                    or "none"
                )
            ),
            (
                "- Dependency Files: "
                + (
                    ", ".join(
                        str(item)
                        for item in _list(dependency_profile.get("dependency_files"))
                    )
                    or "none"
                )
            ),
            (
                "- Packaging Files: "
                + (
                    ", ".join(
                        str(item)
                        for item in _list(dependency_profile.get("packaging_files"))
                    )
                    or "none"
                )
            ),
            (
                "- Test Runner Config Files: "
                + (
                    ", ".join(
                        str(item)
                        for item in _list(
                            dependency_profile.get("test_runner_config_files")
                        )
                    )
                    or "none"
                )
            ),
            "",
            "| Tool | Signal | Evidence | Suggested Install / Setup |",
            "| --- | --- | --- | --- |",
        ]
    )
    for signal in _list(dependency_profile.get("signals")):
        row = _dict(signal)
        lines.append(
            "| "
            f"{_markdown_cell(row.get('tool', ''))} | "
            f"{_markdown_cell(row.get('signal', ''))} | "
            f"{_markdown_cell(', '.join(str(item) for item in _list(row.get('evidence'))))} | "
            f"`{_markdown_cell(row.get('suggested_install') or 'none')}` |"
        )
    if not _list(dependency_profile.get("signals")):
        lines.append("| none | none | none | `none` |")
    lines.extend(
        [
            "",
        "## Layout Hints",
        "",
        ]
    )
    for hint in _list(profile.get("layout_hints")):
        lines.append(f"- {_markdown_cell(hint)}")
    if not _list(profile.get("layout_hints")):
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Project Config Files",
            "",
            "| Path |",
            "| --- |",
        ]
    )
    for path in _list(profile.get("project_config_files")):
        lines.append(f"| `{_markdown_cell(path)}` |")
    if not _list(profile.get("project_config_files")):
        lines.append("| none |")
    lines.extend(
        [
            "",
            "## Top Source Directories",
            "",
            "| Directory | Sources |",
            "| --- | ---: |",
        ]
    )
    for directory, count in _dict(profile.get("top_source_directories")).items():
        lines.append(f"| {_markdown_cell(directory)} | {_int(count)} |")
    if not _dict(profile.get("top_source_directories")):
        lines.append("| none | 0 |")
    return "\n".join(lines)


def _project_config_files(paths: list[str]) -> list[str]:
    config_names = {
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
    matched = []
    for path in paths:
        name = PurePosixPath(path).name
        if name in config_names or path.startswith(".github/workflows/"):
            matched.append(path)
    return sorted(set(matched))


def _dependency_manager_profile(config_files: list[str]) -> dict[str, Any]:
    root_files = [
        path
        for path in sorted(set(config_files))
        if _is_root_project_config(path)
    ]
    names_by_path = {path: PurePosixPath(path).name for path in root_files}
    signals: list[dict[str, Any]] = []

    def add(
        tool: str,
        signal: str,
        evidence_names: set[str],
        suggested_install: str,
    ) -> None:
        evidence = [
            path
            for path, name in names_by_path.items()
            if name in evidence_names
        ]
        if not evidence:
            return
        signals.append(
            {
                "tool": tool,
                "signal": signal,
                "evidence": evidence,
                "suggested_install": suggested_install,
            }
        )

    add("uv", "uv_lock_detected", {"uv.lock"}, "uv sync --dev")
    add("poetry", "poetry_lock_detected", {"poetry.lock"}, "poetry install --with dev")
    add("pdm", "pdm_project_detected", {"pdm.lock", "pdm.toml"}, "pdm install -d")
    add("hatch", "hatch_project_detected", {"hatch.toml"}, "hatch env create")
    add("pipenv", "pipfile_detected", {"Pipfile", "Pipfile.lock"}, "pipenv install --dev")
    add(
        "pip",
        "requirements_detected",
        {"requirements-test.txt", "requirements-dev.txt", "requirements.txt"},
        "python -m pip install -r <requirements-file>",
    )
    add("setuptools", "legacy_packaging_detected", {"setup.cfg", "setup.py"}, "python -m pip install -e .")
    add("pyproject", "pyproject_detected", {"pyproject.toml"}, "python -m pip install -e .")
    add("tox", "tox_config_detected", {"tox.ini"}, "python -m pip install tox")
    add("nox", "nox_config_detected", {"noxfile.py"}, "python -m pip install nox")

    dependency_names = {
        "Pipfile",
        "Pipfile.lock",
        "hatch.toml",
        "pdm.lock",
        "pdm.toml",
        "poetry.lock",
        "pyproject.toml",
        "requirements-dev.txt",
        "requirements-test.txt",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
        "uv.lock",
    }
    packaging_names = {
        "hatch.toml",
        "pdm.toml",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
    }
    lock_names = {
        "Pipfile.lock",
        "pdm.lock",
        "poetry.lock",
        "uv.lock",
    }
    test_runner_names = {"tox.ini", "noxfile.py", "pytest.ini"}
    dependency_files = [
        path for path, name in names_by_path.items() if name in dependency_names
    ]
    packaging_files = [
        path for path, name in names_by_path.items() if name in packaging_names
    ]
    lock_files = [path for path, name in names_by_path.items() if name in lock_names]
    test_runner_config_files = [
        path for path, name in names_by_path.items() if name in test_runner_names
    ]
    tool_signals = sorted(
        {
            str(row.get("tool") or "")
            for row in signals
            if str(row.get("tool") or "")
        }
    )
    return {
        "status": "pass" if signals else "skipped",
        "reason": "dependency_config_detected" if signals else "no_dependency_config_detected",
        "tool_signals": tool_signals,
        "tool_signal_count": len(tool_signals),
        "signals": signals,
        "dependency_files": dependency_files,
        "dependency_file_count": len(dependency_files),
        "packaging_files": packaging_files,
        "packaging_file_count": len(packaging_files),
        "lock_files": lock_files,
        "lock_file_count": len(lock_files),
        "test_runner_config_files": test_runner_config_files,
        "test_runner_config_file_count": len(test_runner_config_files),
    }


def _is_root_project_config(path: str) -> bool:
    pure = PurePosixPath(str(path).replace("\\", "/").strip("/"))
    return bool(pure.parts) and len(pure.parts) == 1


def _package_roots(paths: list[str]) -> list[str]:
    roots = []
    for path in paths:
        pure = PurePosixPath(path)
        if pure.name != "__init__.py":
            continue
        parts = pure.parts
        if len(parts) >= 2 and parts[0] not in {"tests", "test"}:
            roots.append(parts[0])
    return sorted(set(roots))


def _src_layout_packages(paths: list[str]) -> list[str]:
    packages = []
    for path in paths:
        pure = PurePosixPath(path)
        parts = pure.parts
        if len(parts) >= 3 and parts[0] == "src" and pure.name == "__init__.py":
            packages.append(parts[1])
    return sorted(set(packages))


def _recommended_target_prefix(
    *,
    package_roots: list[str],
    src_layout_packages: list[str],
) -> str:
    if len(src_layout_packages) == 1:
        return src_layout_packages[0]
    non_src_roots = [
        root
        for root in package_roots
        if root != "src" and root.lower() not in AUXILIARY_PACKAGE_ROOTS
    ]
    if len(non_src_roots) == 1:
        return non_src_roots[0]
    return ""


def _test_content_profile(
    imported_sources: list[dict[str, Any]],
    test_source_paths: list[str],
) -> dict[str, Any]:
    test_paths = set(test_source_paths)
    pytest_paths: list[str] = []
    unittest_paths: list[str] = []
    inspected_paths: list[str] = []
    for source in imported_sources:
        source_path = _clean_path(
            str(source.get("source_path") or source.get("target_path") or "")
        )
        if not source_path or source_path not in test_paths:
            continue
        text = _local_source_text(source)
        if text is None:
            continue
        inspected_paths.append(source_path)
        if _looks_like_pytest(text):
            pytest_paths.append(source_path)
        if _looks_like_unittest(text):
            unittest_paths.append(source_path)
    return {
        "inspected_test_source_count": len(inspected_paths),
        "inspected_test_source_paths": inspected_paths[:20],
        "pytest_test_source_count": len(pytest_paths),
        "pytest_test_source_paths": pytest_paths[:20],
        "unittest_test_source_count": len(unittest_paths),
        "unittest_test_source_paths": unittest_paths[:20],
    }


def _local_source_text(source: dict[str, Any]) -> str | None:
    for key in ("raw_url", "local_path", "path"):
        value = str(source.get(key) or "")
        if not value:
            continue
        path = Path(value)
        if not path.is_file():
            continue
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                return path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return None
        except OSError:
            return None
    return None


def _looks_like_pytest(text: str) -> bool:
    return bool(
        re.search(r"(^|\n)\s*import\s+pytest\b", text)
        or re.search(r"(^|\n)\s*from\s+pytest\s+import\b", text)
        or "pytest." in text
        or "@pytest." in text
    )


def _looks_like_unittest(text: str) -> bool:
    return bool(
        re.search(r"(^|\n)\s*import\s+unittest\b", text)
        or re.search(r"(^|\n)\s*from\s+unittest\s+import\b", text)
        or "unittest.TestCase" in text
        or re.search(r"\bTestCase\b", text)
    )


def _test_frameworks(
    *,
    raw_paths: list[str],
    test_source_paths: list[str],
    config_files: list[str],
    pytest_test_source_paths: list[str],
    unittest_test_source_paths: list[str],
) -> list[str]:
    signals: set[str] = set()
    names = {PurePosixPath(path).name for path in raw_paths + config_files}
    has_pytest_config = bool({"pytest.ini", "tox.ini"} & names)
    has_pytest_content = bool(pytest_test_source_paths)
    has_unittest_content = bool(unittest_test_source_paths)
    if has_pytest_config or has_pytest_content:
        signals.add("pytest")
    if "noxfile.py" in names:
        signals.add("nox")
    if "tox.ini" in names:
        signals.add("tox")
    if has_unittest_content:
        signals.add("unittest")
    if test_source_paths and not signals:
        signals.add("pytest")
        signals.add("unittest")
    return sorted(signals)


def _framework_profile(
    *,
    raw_paths: list[str],
    imported_paths: list[str],
    config_files: list[str],
) -> dict[str, Any]:
    paths = sorted(set(raw_paths + imported_paths + config_files))
    signals: list[dict[str, Any]] = []
    django_evidence = _django_path_evidence(paths)
    if django_evidence:
        signals.append(
            {
                "framework": "django",
                "signal": "django_project_layout",
                "evidence": django_evidence[:8],
            }
        )
    for framework in ("fastapi", "flask"):
        evidence = _framework_name_evidence(paths, framework)
        if evidence:
            signals.append(
                {
                    "framework": framework,
                    "signal": f"{framework}_path_signal",
                    "evidence": evidence[:8],
                }
            )
    frameworks = sorted(
        {
            str(row.get("framework") or "")
            for row in signals
            if str(row.get("framework") or "")
        }
    )
    django_settings_candidates = (
        _django_settings_module_candidates(paths) if "django" in frameworks else []
    )
    environment_variables: dict[str, str] = {}
    if django_settings_candidates:
        environment_variables["DJANGO_SETTINGS_MODULE"] = str(
            django_settings_candidates[0].get("module") or ""
        )
    return {
        "status": "pass" if frameworks else "skipped",
        "reason": (
            "framework_signals_detected" if frameworks else "no_framework_signals"
        ),
        "frameworks": frameworks,
        "signals": signals,
        "django_settings_candidates": django_settings_candidates[:8],
        "environment_variables": {
            key: value for key, value in environment_variables.items() if value
        },
    }


def _django_path_evidence(paths: list[str]) -> list[str]:
    evidence: list[str] = []
    for path in paths:
        normalized = _clean_path(path)
        pure = PurePosixPath(normalized)
        lower = normalized.lower()
        if pure.name == "manage.py":
            evidence.append(normalized)
        elif pure.name in {"settings.py", "urls.py", "wsgi.py", "asgi.py"}:
            evidence.append(normalized)
        elif "/migrations/" in f"/{lower}/":
            evidence.append(normalized)
    return sorted(set(evidence))


def _framework_name_evidence(paths: list[str], framework: str) -> list[str]:
    evidence: list[str] = []
    token = framework.lower()
    for path in paths:
        normalized = _clean_path(path)
        parts = [part.lower() for part in PurePosixPath(normalized).parts]
        name = PurePosixPath(normalized).name.lower()
        if token in parts or token in name:
            evidence.append(normalized)
    return sorted(set(evidence))


def _django_settings_module_candidates(paths: list[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(set(_clean_path(item) for item in paths)):
        pure = PurePosixPath(path)
        parts = pure.parts
        if not parts or parts[0] in {"test", "tests"}:
            continue
        module = ""
        reason = ""
        confidence = 0.0
        if pure.name == "settings.py":
            module = _module_name_from_path(path)
            reason = "settings_py"
            confidence = 0.86
        elif len(parts) >= 2 and parts[-2] == "settings" and pure.suffix == ".py":
            module = _module_name_from_path(path)
            reason = "settings_package_module"
            confidence = 0.74 if pure.name == "base.py" else 0.68
        elif pure.name == "__init__.py" and len(parts) >= 2 and parts[-2] == "settings":
            module = _module_name_from_path(str(PurePosixPath(*parts[:-1])))
            reason = "settings_package"
            confidence = 0.70
        if module:
            candidates.append(
                {
                    "module": module,
                    "source_path": path,
                    "confidence": round(confidence, 4),
                    "reason": reason,
                }
            )
    return sorted(
        _dedupe_django_settings_candidates(candidates),
        key=lambda row: (-_float(row.get("confidence", 0.0)), str(row.get("module", ""))),
    )


def _module_name_from_path(path: str) -> str:
    pure = PurePosixPath(path)
    parts = list(pure.parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts:
        parts[-1] = PurePosixPath(parts[-1]).stem
    return ".".join(part for part in parts if part)


def _dedupe_django_settings_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        module = str(candidate.get("module") or "")
        if not module or module in seen:
            continue
        seen.add(module)
        deduped.append(candidate)
    return deduped


def _recommended_test_command(
    *,
    test_frameworks: list[str],
    test_source_paths: list[str],
    config_files: list[str],
    test_command_candidates: list[dict[str, Any]],
) -> str:
    if test_command_candidates:
        return str(test_command_candidates[0].get("command") or "")
    names = {PurePosixPath(path).name for path in config_files}
    if "tox" in test_frameworks:
        return "python -m tox"
    if "nox" in test_frameworks:
        return "python -m nox"
    if "pytest" in test_frameworks:
        return "python -m pytest"
    if test_source_paths:
        return "python -m unittest discover"
    if "pyproject.toml" in names or "setup.py" in names or "setup.cfg" in names:
        return "python -m pytest"
    return ""


def _test_command_candidates(
    *,
    test_frameworks: list[str],
    test_source_paths: list[str],
    config_files: list[str],
    pytest_test_source_paths: list[str],
    unittest_test_source_paths: list[str],
) -> list[dict[str, Any]]:
    names = {PurePosixPath(path).name for path in config_files}
    candidates: list[dict[str, Any]] = []
    if "tox" in test_frameworks:
        candidates.append(
            _command_candidate(
                command="python -m tox",
                runner="tox",
                confidence=0.92,
                reason="tox_ini_detected",
                evidence=_matching_paths(config_files, {"tox.ini"}),
                scope="tox managed environments",
            )
        )
    if "nox" in test_frameworks:
        candidates.append(
            _command_candidate(
                command="python -m nox",
                runner="nox",
                confidence=0.90,
                reason="noxfile_detected",
                evidence=_matching_paths(config_files, {"noxfile.py"}),
                scope="nox managed sessions",
            )
        )
    pytest_evidence = _matching_paths(
        config_files,
        {"pytest.ini", "setup.cfg", "tox.ini"},
    )
    if "pytest" in test_frameworks or pytest_evidence or (
        test_source_paths and not unittest_test_source_paths
    ):
        confidence = 0.86 if "pytest" in test_frameworks else 0.72
        candidates.append(
            _command_candidate(
                command="python -m pytest",
                runner="pytest",
                confidence=confidence,
                reason=(
                    "pytest_config_or_tests_detected"
                    if pytest_evidence or "pytest" in test_frameworks
                    else "python_test_files_detected"
                ),
                evidence=[*pytest_evidence, *_sample_paths(test_source_paths, limit=3)],
                scope="pytest repository discovery",
            )
        )
    if test_source_paths:
        unittest_confidence = 0.82 if unittest_test_source_paths else 0.55
        candidates.append(
            _command_candidate(
                command="python -m unittest discover",
                runner="unittest",
                confidence=unittest_confidence,
                reason=(
                    "unittest_testcase_detected"
                    if unittest_test_source_paths
                    else "python_test_files_fallback"
                ),
                evidence=(
                    _sample_paths(unittest_test_source_paths, limit=3)
                    if unittest_test_source_paths
                    else _sample_paths(test_source_paths, limit=3)
                ),
                scope=(
                    "stdlib unittest TestCase discovery"
                    if unittest_test_source_paths
                    else "stdlib unittest discovery fallback"
                ),
            )
        )
    if not candidates and {"pyproject.toml", "setup.py", "setup.cfg"} & names:
        candidates.append(
            _command_candidate(
                command="python -m pytest",
                runner="pytest",
                confidence=0.50,
                reason="python_project_without_test_files_fallback",
                evidence=_matching_paths(
                    config_files,
                    {"pyproject.toml", "setup.py", "setup.cfg"},
                ),
                scope="best effort Python project smoke test",
            )
        )
    return _rank_candidates(_dedupe_candidates(candidates))


def _command_candidate(
    *,
    command: str,
    runner: str,
    confidence: float,
    reason: str,
    evidence: list[str],
    scope: str,
) -> dict[str, Any]:
    return {
        "command": command,
        "runner": runner,
        "confidence": round(confidence, 4),
        "reason": reason,
        "evidence": evidence[:8],
        "scope": scope,
    }


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        command = str(candidate.get("command") or "")
        if not command or command in seen:
            continue
        seen.add(command)
        deduped.append(candidate)
    return deduped


def _rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for index, candidate in enumerate(candidates, start=1):
        row = dict(candidate)
        row["rank"] = index
        row["recommended"] = index == 1
        ranked.append(row)
    return ranked


def _repository_doctor(
    *,
    discovery_item_count: int,
    imported_source_count: int,
    python_source_ratio: float,
    test_source_count: int,
    project_config_count: int,
    recommended_test_command: str,
) -> dict[str, Any]:
    checks = [
        _doctor_check(
            name="discovery_items",
            passed=discovery_item_count > 0,
            expected=">0",
            actual=str(discovery_item_count),
            message="GitHub discovery returned repository paths.",
        ),
        _doctor_check(
            name="python_sources",
            passed=imported_source_count > 0,
            expected=">0",
            actual=str(imported_source_count),
            message="Python sources survived import filters.",
        ),
        _doctor_check(
            name="python_source_ratio",
            passed=python_source_ratio >= 0.10 or imported_source_count >= 10,
            expected=">=0.10 or >=10 imported sources",
            actual=f"{python_source_ratio:.4f}/{imported_source_count}",
            message="Repository has enough Python surface for source mining.",
        ),
        _doctor_check(
            name="test_or_config_signal",
            passed=test_source_count > 0 or project_config_count > 0,
            expected="test files or project config",
            actual=f"tests={test_source_count}, configs={project_config_count}",
            message="Repository exposes test files or Python project config.",
        ),
        _doctor_check(
            name="recommended_test_command",
            passed=bool(recommended_test_command),
            expected="non-empty command",
            actual=recommended_test_command or "none",
            message="Profile can recommend a repository-test command.",
        ),
    ]
    failed = [check for check in checks if check["status"] == "fail"]
    blocker = "none"
    status = "pass"
    next_action = "Run preflight smoke or repository-test repair using the recommended command."
    if failed:
        first = failed[0]
        blocker = str(first.get("name") or "")
        status = "fail" if blocker in {"discovery_items", "python_sources"} else "warn"
        next_action = _doctor_next_action(blocker)
    score = _ratio(len(checks) - len(failed), len(checks))
    return {
        "status": status,
        "blocker": blocker,
        "score": score,
        "next_action": next_action,
        "checks": checks,
    }


def _doctor_check(
    *,
    name: str,
    passed: bool,
    expected: str,
    actual: str,
    message: str,
) -> dict[str, str]:
    return {
        "name": name,
        "status": "pass" if passed else "fail",
        "expected": expected,
        "actual": actual,
        "message": message,
    }


def _doctor_next_action(blocker: str) -> str:
    if blocker == "discovery_items":
        return "Check the GitHub repo/ref or discovery query before onboarding."
    if blocker == "python_sources":
        return "Adjust include/exclude filters or target a Python package path."
    if blocker == "python_source_ratio":
        return "Use --include or target_prefix to focus onboarding on the Python package."
    if blocker == "test_or_config_signal":
        return "Provide repository-test options manually or run benchmark-only smoke first."
    if blocker == "recommended_test_command":
        return "Provide an explicit repository test command before repair validation."
    return "Inspect repository profile before running smoke onboarding."


def _matching_paths(paths: list[str], names: set[str]) -> list[str]:
    return [path for path in paths if PurePosixPath(path).name in names]


def _sample_paths(paths: list[str], *, limit: int) -> list[str]:
    return list(paths[: max(0, limit)])


def _layout_hints(
    *,
    imported_paths: list[str],
    config_files: list[str],
    test_source_paths: list[str],
    package_roots: list[str],
    src_layout_packages: list[str],
    recommended_target_prefix: str,
) -> list[str]:
    hints: list[str] = []
    if src_layout_packages:
        hints.append(
            "src-layout package detected: "
            + ", ".join(src_layout_packages[:5])
        )
    if package_roots:
        hints.append("package roots detected: " + ", ".join(package_roots[:5]))
    if recommended_target_prefix:
        hints.append(
            f"use target_prefix={recommended_target_prefix} when materializing flat GitHub sources"
        )
    if test_source_paths:
        hints.append(f"test files detected: {len(test_source_paths)}")
    if config_files:
        hints.append("project config files detected: " + ", ".join(config_files[:5]))
    if imported_paths and not package_roots:
        hints.append("no package __init__.py detected in imported Python sources")
    return hints


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


def _clean_path(value: str) -> str:
    return value.replace("\\", "/").strip().lstrip("/")


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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
