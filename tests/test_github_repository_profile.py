from code_intelligence_agent.evaluation.github_repository_profile import (
    build_github_repository_profile,
    render_github_repository_profile_markdown,
)


def test_repository_profile_ranks_managed_test_command_candidates():
    profile = build_github_repository_profile(
        {
            "tree": [
                {"path": "tox.ini"},
                {"path": "noxfile.py"},
                {"path": "pyproject.toml"},
                {"path": "tests/test_api.py"},
                {"path": "src/demo/__init__.py"},
                {"path": "src/demo/api.py"},
            ]
        },
        _import_report(
            raw_paths=[
                "tox.ini",
                "noxfile.py",
                "pyproject.toml",
                "tests/test_api.py",
                "src/demo/__init__.py",
                "src/demo/api.py",
            ],
            imported_paths=[
                "tests/test_api.py",
                "src/demo/__init__.py",
                "src/demo/api.py",
            ],
        ),
    )

    commands = [candidate["command"] for candidate in profile["test_command_candidates"]]
    markdown = render_github_repository_profile_markdown(profile)

    assert profile["recommended_test_command"] == "python -m tox"
    assert profile["recommended_target_prefix"] == "demo"
    assert profile["layout_type"] == "src_layout"
    assert profile["layout_profile"]["confidence"] == 0.9
    assert profile["layout_profile"]["recommended_analysis_roots"] == ["src/demo"]
    assert profile["recommended_analysis_roots"] == ["src/demo"]
    assert profile["monorepo_candidate"] is False
    assert profile["doctor_status"] == "pass"
    assert profile["doctor_blocker"] == "none"
    assert profile["repository_doctor"]["score"] == 1.0
    assert profile["dependency_tool_signals"] == ["nox", "pyproject", "tox"]
    assert profile["dependency_manager_profile"]["test_runner_config_files"] == [
        "noxfile.py",
        "tox.ini",
    ]
    assert profile["test_command_candidate_count"] == 4
    assert commands == [
        "python -m tox",
        "python -m nox",
        "python -m pytest",
        "python -m unittest discover",
    ]
    assert profile["test_command_candidates"][0]["recommended"] is True
    assert profile["test_command_candidates"][0]["reason"] == "tox_ini_detected"
    assert "Test Command Candidates" in markdown
    assert "Dependency And Packaging Profile" in markdown
    assert "Layout Profile" in markdown
    assert "Layout Type: `src_layout`" in markdown
    assert "python -m pip install tox" in markdown
    assert "Repository Doctor Status: pass" in markdown
    assert "python -m tox" in markdown


def test_repository_profile_detects_modern_dependency_config_and_pytest_fallback():
    profile = build_github_repository_profile(
        {
            "tree": [
                {"path": "pyproject.toml"},
                {"path": "uv.lock"},
                {"path": "src/toolkit/__init__.py"},
                {"path": "src/toolkit/core.py"},
            ]
        },
        _import_report(
            raw_paths=[
                "pyproject.toml",
                "uv.lock",
                "src/toolkit/__init__.py",
                "src/toolkit/core.py",
            ],
            imported_paths=[
                "src/toolkit/__init__.py",
                "src/toolkit/core.py",
            ],
        ),
    )

    assert profile["project_config_files"] == ["pyproject.toml", "uv.lock"]
    assert profile["dependency_tool_signals"] == ["pyproject", "uv"]
    assert profile["dependency_file_count"] == 2
    assert profile["packaging_file_count"] == 1
    assert profile["dependency_manager_profile"]["dependency_files"] == [
        "pyproject.toml",
        "uv.lock",
    ]
    assert profile["dependency_manager_profile"]["lock_files"] == ["uv.lock"]
    assert profile["recommended_test_command"] == "python -m pytest"
    assert profile["doctor_status"] == "pass"
    assert profile["test_command_candidate_count"] == 1
    assert profile["test_command_candidates"][0]["reason"] == (
        "python_project_without_test_files_fallback"
    )


def test_repository_profile_summarizes_dependency_and_packaging_signals():
    profile = build_github_repository_profile(
        {
            "tree": [
                {"path": "pyproject.toml"},
                {"path": "uv.lock"},
                {"path": "poetry.lock"},
                {"path": "pdm.toml"},
                {"path": "hatch.toml"},
                {"path": "Pipfile"},
                {"path": "requirements-dev.txt"},
                {"path": "setup.cfg"},
                {"path": "setup.py"},
                {"path": "tox.ini"},
                {"path": "noxfile.py"},
                {"path": "src/demo/__init__.py"},
                {"path": "src/demo/core.py"},
            ]
        },
        _import_report(
            raw_paths=[
                "pyproject.toml",
                "uv.lock",
                "poetry.lock",
                "pdm.toml",
                "hatch.toml",
                "Pipfile",
                "requirements-dev.txt",
                "setup.cfg",
                "setup.py",
                "tox.ini",
                "noxfile.py",
                "src/demo/__init__.py",
                "src/demo/core.py",
            ],
            imported_paths=[
                "src/demo/__init__.py",
                "src/demo/core.py",
            ],
        ),
    )
    markdown = render_github_repository_profile_markdown(profile)
    dependency_profile = profile["dependency_manager_profile"]

    assert profile["dependency_tool_signals"] == [
        "hatch",
        "nox",
        "pdm",
        "pip",
        "pipenv",
        "poetry",
        "pyproject",
        "setuptools",
        "tox",
        "uv",
    ]
    assert dependency_profile["status"] == "pass"
    assert dependency_profile["reason"] == "dependency_config_detected"
    assert dependency_profile["dependency_file_count"] == 9
    assert dependency_profile["packaging_file_count"] == 5
    assert dependency_profile["lock_files"] == ["poetry.lock", "uv.lock"]
    assert dependency_profile["test_runner_config_files"] == [
        "noxfile.py",
        "tox.ini",
    ]
    assert "Dependency And Packaging Profile" in markdown
    assert "uv sync --dev" in markdown
    assert "poetry install --with dev" in markdown
    assert "python -m pip install tox" in markdown
    assert "python -m pip install nox" in markdown


def test_repository_profile_prefers_unittest_when_testcase_content_detected(tmp_path):
    test_file = tmp_path / "test_service.py"
    test_file.write_text(
        "import unittest\n\n"
        "class ServiceTest(unittest.TestCase):\n"
        "    def test_smoke(self):\n"
        "        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    profile = build_github_repository_profile(
        {
            "tree": [
                {"path": "pkg/service.py"},
                {"path": "tests/test_service.py"},
            ]
        },
        {
            "input_count": 2,
            "source_count": 2,
            "skipped_count": 0,
            "rows": [
                {"status": "imported", "source_path": "pkg/service.py", "reason": ""},
                {
                    "status": "imported",
                    "source_path": "tests/test_service.py",
                    "reason": "",
                },
            ],
            "source_entries": [
                {"source_path": "pkg/service.py", "target_path": "pkg/service.py"},
                {
                    "source_path": "tests/test_service.py",
                    "target_path": "tests/test_service.py",
                    "raw_url": str(test_file),
                },
            ],
        },
    )

    assert profile["test_content_profile"]["unittest_test_source_paths"] == [
        "tests/test_service.py"
    ]
    assert profile["recommended_test_command"] == "python -m unittest discover"
    assert profile["test_command_candidates"][0]["runner"] == "unittest"
    assert profile["test_command_candidates"][0]["reason"] == (
        "unittest_testcase_detected"
    )


def test_repository_profile_ignores_auxiliary_roots_when_recommending_target_prefix():
    profile = build_github_repository_profile(
        {
            "tree": [
                {"path": "tox.ini"},
                {"path": "bench/__init__.py"},
                {"path": "bench/benchmarks.py"},
                {"path": "h11/__init__.py"},
                {"path": "h11/_readers.py"},
                {"path": "h11/tests/test_readers.py"},
            ]
        },
        _import_report(
            raw_paths=[
                "tox.ini",
                "bench/__init__.py",
                "bench/benchmarks.py",
                "h11/__init__.py",
                "h11/_readers.py",
                "h11/tests/test_readers.py",
            ],
            imported_paths=[
                "bench/__init__.py",
                "bench/benchmarks.py",
                "h11/__init__.py",
                "h11/_readers.py",
                "h11/tests/test_readers.py",
            ],
        ),
    )

    assert profile["package_roots"] == ["bench", "h11"]
    assert profile["layout_type"] == "single_package"
    assert profile["recommended_target_prefix"] == "h11"
    assert (
        "use target_prefix=h11 when materializing flat GitHub sources"
        in profile["layout_hints"]
    )


def test_repository_profile_classifies_partial_monorepo_candidate():
    profile = build_github_repository_profile(
        {
            "tree": [
                {"path": "services/api/pyproject.toml"},
                {"path": "services/api/src/api/__init__.py"},
                {"path": "services/api/src/api/routes.py"},
                {"path": "services/worker/pyproject.toml"},
                {"path": "services/worker/src/worker/__init__.py"},
                {"path": "services/worker/src/worker/jobs.py"},
                {"path": "libs/common/common/__init__.py"},
                {"path": "libs/common/common/util.py"},
                {"path": "tests/test_smoke.py"},
            ]
        },
        _import_report(
            raw_paths=[
                "services/api/pyproject.toml",
                "services/api/src/api/__init__.py",
                "services/api/src/api/routes.py",
                "services/worker/pyproject.toml",
                "services/worker/src/worker/__init__.py",
                "services/worker/src/worker/jobs.py",
                "libs/common/common/__init__.py",
                "libs/common/common/util.py",
                "tests/test_smoke.py",
            ],
            imported_paths=[
                "services/api/src/api/__init__.py",
                "services/api/src/api/routes.py",
                "services/worker/src/worker/__init__.py",
                "services/worker/src/worker/jobs.py",
                "libs/common/common/__init__.py",
                "libs/common/common/util.py",
                "tests/test_smoke.py",
            ],
        ),
    )
    markdown = render_github_repository_profile_markdown(profile)

    assert profile["layout_type"] == "monorepo_candidate"
    assert profile["monorepo_candidate"] is True
    assert profile["layout_profile"]["application_roots"] == [
        "libs",
        "services",
    ]
    assert profile["layout_profile"]["nested_project_config_roots"] == [
        "services/api",
        "services/worker",
    ]
    assert profile["layout_profile"]["recommended_analysis_roots"] == [
        "services/api",
        "services/worker",
    ]
    nested_candidates = [
        (candidate["runner"], candidate["working_dir"], candidate["reason"])
        for candidate in profile["test_command_candidates"]
        if candidate.get("working_dir")
    ]
    assert ("pytest", "services/api", "nested_pytest_config_or_tests_detected") in (
        nested_candidates
    )
    assert ("pytest", "services/worker", "nested_pytest_config_or_tests_detected") in (
        nested_candidates
    )
    assert profile["recommended_test_working_dir"] == "services/api"
    assert "multiple_project_roots_or_nested_configs" in (
        profile["layout_profile"]["reason"]
    )
    assert "Layout Type: `monorepo_candidate`" in markdown
    assert "Monorepo Candidate: true" in markdown


def test_repository_profile_detects_django_framework_settings_module():
    profile = build_github_repository_profile(
        {
            "tree": [
                {"path": "manage.py"},
                {"path": "mysite/settings.py"},
                {"path": "mysite/urls.py"},
                {"path": "mysite/wsgi.py"},
                {"path": "tests/test_views.py"},
            ]
        },
        _import_report(
            raw_paths=[
                "manage.py",
                "mysite/settings.py",
                "mysite/urls.py",
                "mysite/wsgi.py",
                "tests/test_views.py",
            ],
            imported_paths=[
                "manage.py",
                "mysite/settings.py",
                "mysite/urls.py",
                "mysite/wsgi.py",
                "tests/test_views.py",
            ],
        ),
    )
    markdown = render_github_repository_profile_markdown(profile)

    assert profile["framework_signals"] == ["django"]
    assert profile["framework_profile"]["environment_variables"] == {
        "DJANGO_SETTINGS_MODULE": "mysite.settings"
    }
    assert profile["framework_profile"]["django_settings_candidates"][0]["module"] == (
        "mysite.settings"
    )
    assert "Framework Profile" in markdown
    assert "DJANGO_SETTINGS_MODULE=mysite.settings" in markdown


def test_repository_profile_doctor_flags_missing_python_sources():
    profile = build_github_repository_profile(
        {"tree": [{"path": "README.md"}, {"path": "docs/usage.md"}]},
        _import_report(
            raw_paths=["README.md", "docs/usage.md"],
            imported_paths=[],
        ),
    )
    markdown = render_github_repository_profile_markdown(profile)

    assert profile["doctor_status"] == "fail"
    assert profile["doctor_blocker"] == "python_sources"
    assert profile["doctor_score"] < 1.0
    assert "Adjust include/exclude filters" in profile["doctor_next_action"]
    assert "Repository Doctor Checks" in markdown
    assert "python_sources" in markdown


def test_repository_profile_doctor_warns_without_test_or_config_signal():
    profile = build_github_repository_profile(
        {
            "tree": [
                {"path": "pkg/core.py"},
                {"path": "pkg/helpers.py"},
            ]
        },
        _import_report(
            raw_paths=["pkg/core.py", "pkg/helpers.py"],
            imported_paths=["pkg/core.py", "pkg/helpers.py"],
        ),
    )

    assert profile["doctor_status"] == "warn"
    assert profile["doctor_blocker"] == "test_or_config_signal"
    assert profile["recommended_test_command"] == ""
    assert "benchmark-only smoke" in profile["doctor_next_action"]


def _import_report(*, raw_paths: list[str], imported_paths: list[str]) -> dict:
    return {
        "input_count": len(raw_paths),
        "source_count": len(imported_paths),
        "skipped_count": max(0, len(raw_paths) - len(imported_paths)),
        "rows": [
            {
                "status": "imported" if path in imported_paths else "skipped",
                "source_path": path,
                "reason": "" if path in imported_paths else "non_python",
            }
            for path in raw_paths
        ],
        "source_entries": [
            {"source_path": path, "target_path": path} for path in imported_paths
        ],
    }
