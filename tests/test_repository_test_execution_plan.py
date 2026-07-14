from pathlib import Path

from code_intelligence_agent.evaluation.repository_test_execution_plan import (
    plan_repository_test_execution,
    render_repository_test_execution_plan_markdown,
    write_repository_test_execution_plan_artifacts,
)


def test_repository_test_execution_plan_recommends_narrow_pytest_command(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "def test_smoke():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m pytest",
            "test_source_paths": ["tests/test_sample.py", "tests/test_other.py"],
        },
        repository_test_environment={
            "status": "pass",
            "test_tool_available": True,
            "recommended_install_command": "python -m pip install -e .",
        },
        repository_root=tmp_path,
        max_narrow_tests=1,
    )
    paths = write_repository_test_execution_plan_artifacts(payload, tmp_path / "out")
    markdown = render_repository_test_execution_plan_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "execution_plan_built"
    assert payload["recommended_execution_level"] == "narrow"
    assert payload["recommended_execution_risk"] == "low"
    assert payload["recommended_execution_command"] == (
        "python -m pytest -q tests/test_sample.py"
    )
    assert payload["executable_now"] is True
    assert payload["selected_test_paths"] == ["tests/test_sample.py"]
    assert payload["missing_selected_test_paths"] == []
    assert payload["candidate_commands"][0]["recommended"] is True
    assert "Repository Test Execution Plan" in markdown
    assert Path(paths["repository_test_execution_plan_json"]).exists()
    assert Path(paths["repository_test_execution_plan_markdown"]).exists()


def test_repository_test_execution_plan_recommends_narrow_unittest_discovery(
    tmp_path,
):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "import unittest\n\n"
        "class SampleTest(unittest.TestCase):\n"
        "    def test_smoke(self):\n"
        "        self.assertTrue(True)\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m unittest discover",
            "test_source_paths": ["tests/test_sample.py", "tests/test_other.py"],
        },
        repository_test_environment={
            "status": "pass",
            "test_tool_available": True,
            "test_module": "unittest",
        },
        repository_root=tmp_path,
        max_narrow_tests=1,
    )
    markdown = render_repository_test_execution_plan_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "execution_plan_built"
    assert payload["recommended_execution_runner"] == "unittest"
    assert payload["recommended_execution_level"] == "narrow"
    assert payload["recommended_execution_risk"] == "low"
    assert payload["recommended_execution_command"] == (
        "python -m unittest discover -s tests -p test_sample.py"
    )
    assert payload["executable_now"] is True
    assert payload["selected_test_paths"] == ["tests/test_sample.py"]
    assert payload["missing_selected_test_paths"] == []
    assert payload["candidate_commands"][0]["recommended"] is True
    assert payload["candidate_commands"][0]["reason"] == (
        "unittest_test_file_selection"
    )
    assert any(
        candidate["command"] == "python -m unittest discover"
        and candidate["level"] == "full"
        for candidate in payload["candidate_commands"]
    )
    assert "unittest_test_file_selection" in markdown


def test_repository_test_execution_plan_skips_package_init_for_unittest_fallback(
    tmp_path,
):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text(
        "# package marker\n",
        encoding="utf-8",
    )
    (tests_dir / "test_simple.py").write_text(
        "import unittest\n\n"
        "class TestSimple(unittest.TestCase):\n"
        "    def test_add_one(self):\n"
        "        self.assertEqual(1 + 1, 2)\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m nox",
            "test_source_paths": ["tests/__init__.py", "tests/test_simple.py"],
            "test_command_candidates": [
                {
                    "rank": 1,
                    "command": "python -m nox",
                    "runner": "nox",
                    "confidence": 0.9,
                    "reason": "noxfile_detected",
                },
                {
                    "rank": 2,
                    "command": "python -m unittest discover",
                    "runner": "unittest",
                    "confidence": 0.82,
                    "reason": "unittest_testcase_detected",
                },
            ],
        },
        repository_test_environment={
            "status": "warning",
            "reason": "test_tool_missing",
            "test_module": "nox",
            "test_tool_available": False,
        },
        repository_root=tmp_path,
        max_narrow_tests=1,
    )

    assert payload["status"] == "pass"
    assert payload["recommended_execution_runner"] == "unittest"
    assert payload["recommended_execution_command"] == (
        "python -m unittest discover -s tests -p test_simple.py"
    )
    assert payload["selected_test_paths"] == ["tests/test_simple.py"]
    assert payload["runner_fallback_used"] is True
    assert payload["runner_fallback_reason"] == "missing_runner:nox"
    assert payload["runner_fallback_from"] == "nox"
    assert payload["runner_fallback_to"] == "unittest"
    assert payload["executable_now"] is True
    assert payload["candidate_commands"][0]["selected_test_paths"] == [
        "tests/test_simple.py"
    ]


def test_repository_test_execution_plan_warns_without_checkout():
    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m pytest",
            "test_source_paths": ["tests/test_sample.py"],
        },
        repository_test_environment={"status": "pass", "test_tool_available": True},
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "full_repo_not_materialized"
    assert payload["recommended_execution_level"] == "narrow"
    assert payload["executable_now"] is False
    assert any("--checkout-repository-tests" in action for action in payload["next_actions"])


def test_repository_test_execution_plan_warns_when_selected_test_missing(tmp_path):
    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m pytest",
            "test_source_paths": ["tests/test_missing.py"],
        },
        repository_test_environment={"status": "pass", "test_tool_available": True},
        repository_root=tmp_path,
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "selected_tests_missing_in_checkout"
    assert payload["executable_now"] is False
    assert payload["missing_selected_test_paths"] == ["tests/test_missing.py"]


def test_repository_test_execution_plan_skips_without_command():
    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "",
            "test_source_paths": ["tests/test_sample.py"],
        }
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "no_recommended_test_command"
    assert payload["candidate_commands"] == []
    assert payload["recommended_execution_command"] == ""


def test_repository_test_execution_plan_can_start_from_ci_candidate_without_profile_command(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "def test_smoke():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "",
            "test_source_paths": [],
        },
        repository_test_environment={
            "status": "pass",
            "ci_configuration": {
                "test_commands": [
                    {
                        "command": "python -m pytest --tb=short tests",
                        "reason": "github_actions_test_step",
                    }
                ]
            },
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "pass"
    assert payload["reason"] == "execution_plan_built"
    assert payload["recommended_execution_command"] == (
        "python -m pytest --tb=short tests"
    )
    assert payload["recommended_execution_level"] == "ci"
    assert payload["recommended_execution_runner"] == "pytest"
    assert payload["executable_now"] is True


def test_repository_test_execution_plan_keeps_tox_full_command():
    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m tox",
            "test_source_paths": ["tests/test_sample.py"],
        },
        repository_test_environment={"status": "warning", "test_tool_available": False},
        repository_root=".",
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "test_environment_warning"
    assert payload["recommended_execution_level"] == "full"
    assert payload["recommended_execution_risk"] == "high"
    assert payload["recommended_execution_command"] == "python -m tox"
    assert payload["preferred_test_runner"] == "tox"
    assert payload["runner_fallback_used"] is False
    assert payload["runner_fallback_reason"] == ""
    assert payload["executable_now"] is False


def test_repository_test_execution_plan_uses_prepared_pytest_runner_probe(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "def test_smoke():\n    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m tox",
            "test_source_paths": ["tests/test_sample.py"],
            "test_command_candidates": [
                {
                    "rank": 1,
                    "command": "python -m tox",
                    "runner": "tox",
                    "confidence": 0.9,
                    "reason": "tox_ini_detected",
                }
            ],
        },
        repository_test_environment={
            "status": "warning",
            "reason": "test_tool_missing",
            "test_module": "tox",
            "test_tool_available": False,
        },
        repository_test_environment_setup={
            "status": "pass",
            "setup_mode": "runner_probe",
            "test_module": "pytest",
        },
        repository_test_environment_setup_result={
            "status": "pass",
            "executed": True,
            "setup_mode": "runner_probe",
            "test_module": "pytest",
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "pass"
    assert payload["prepared_test_runner"] == "pytest"
    assert payload["recommended_execution_runner"] == "pytest"
    assert payload["recommended_execution_level"] == "narrow"
    assert payload["recommended_execution_command"] == (
        "python -m pytest -q tests/test_sample.py"
    )
    assert payload["recommended_execution_source"] == "prepared_runner_probe"
    assert payload["runner_fallback_used"] is True
    assert payload["runner_fallback_reason"] == "missing_runner:tox"
    assert payload["executable_now"] is True
    assert any(
        candidate["runner"] == "tox" for candidate in payload["candidate_commands"]
    )


def test_repository_test_execution_plan_blocks_unprepared_runner_probe(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "def test_smoke():\n    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m pytest",
            "test_source_paths": ["tests/test_sample.py"],
        },
        repository_test_environment={
            "status": "warning",
            "reason": "python_version_incompatible",
            "test_module": "pytest",
            "test_tool_available": True,
        },
        repository_test_environment_setup={
            "status": "warning",
            "reason": "python_version_incompatible",
            "setup_mode": "runner_probe",
            "test_module": "pytest",
        },
        repository_test_environment_setup_result={
            "status": "skipped",
            "executed": False,
            "reason": "setup_plan_not_ready",
            "setup_mode": "runner_probe",
            "test_module": "pytest",
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "runner_probe_setup_not_ready"
    assert payload["runner_probe_setup_required"] is True
    assert payload["runner_probe_setup_ready"] is False
    assert payload["prepared_test_runner"] == ""
    assert payload["executable_now"] is False
    assert any(
        "Do not fall back to the current interpreter" in action
        for action in payload["next_actions"]
    )


def test_repository_test_execution_plan_uses_pytest_fallback_when_tox_missing(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "def test_smoke():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m tox",
            "test_source_paths": ["tests/test_sample.py"],
            "test_command_candidates": [
                {
                    "rank": 1,
                    "command": "python -m tox",
                    "runner": "tox",
                    "confidence": 0.92,
                    "reason": "tox_ini_detected",
                },
                {
                    "rank": 2,
                    "command": "python -m pytest",
                    "runner": "pytest",
                    "confidence": 0.86,
                    "reason": "pytest_config_or_tests_detected",
                },
            ],
        },
        repository_test_environment={
            "status": "warning",
            "reason": "test_tool_missing",
            "test_module": "tox",
            "test_tool_available": False,
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "pass"
    assert payload["reason"] == "execution_plan_built"
    assert payload["recommended_execution_command"] == (
        "python -m pytest -q tests/test_sample.py"
    )
    assert payload["recommended_execution_level"] == "narrow"
    assert payload["recommended_execution_runner"] == "pytest"
    assert payload["preferred_test_runner"] == "tox"
    assert payload["recommended_execution_source"] == "profile_fallback"
    assert payload["runner_fallback_used"] is True
    assert payload["runner_fallback_reason"] == "missing_runner:tox"
    assert payload["runner_fallback_from"] == "tox"
    assert payload["runner_fallback_to"] == "pytest"
    assert payload["executable_now"] is True
    assert payload["profile_test_command_candidate_count"] == 2
    assert payload["candidate_commands"][0]["recommended"] is True
    assert payload["candidate_commands"][0]["source"] == "profile_fallback"
    assert any(
        candidate["command"] == "python -m tox"
        and candidate["source"] == "profile_recommended"
        for candidate in payload["candidate_commands"]
    )


def test_repository_test_execution_plan_preserves_monorepo_working_dirs(tmp_path):
    api_tests = tmp_path / "services" / "api" / "tests"
    worker_tests = tmp_path / "services" / "worker" / "tests"
    api_tests.mkdir(parents=True)
    worker_tests.mkdir(parents=True)
    (api_tests / "test_api.py").write_text("def test_api():\n    assert True\n")
    (worker_tests / "test_worker.py").write_text(
        "def test_worker():\n    assert True\n"
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m pytest",
            "recommended_test_working_dir": "services/api",
            "test_source_paths": [
                "services/api/tests/test_api.py",
                "services/worker/tests/test_worker.py",
            ],
            "test_command_candidates": [
                {
                    "rank": 1,
                    "command": "python -m pytest",
                    "runner": "pytest",
                    "confidence": 0.82,
                    "reason": "nested_pytest_config_or_tests_detected",
                    "working_dir": "services/api",
                },
                {
                    "rank": 2,
                    "command": "python -m pytest",
                    "runner": "pytest",
                    "confidence": 0.82,
                    "reason": "nested_pytest_config_or_tests_detected",
                    "working_dir": "services/worker",
                },
            ],
        },
        repository_test_environment={"status": "pass", "test_tool_available": True},
        repository_root=tmp_path,
    )
    markdown = render_repository_test_execution_plan_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "execution_plan_built"
    assert payload["recommended_working_dir"] == "services/api"
    assert payload["recommended_execution_cwd"] == str(tmp_path / "services" / "api")
    assert payload["recommended_execution_command"] == (
        "python -m pytest -q tests/test_api.py"
    )
    assert payload["selected_test_paths"] == ["tests/test_api.py"]
    assert payload["missing_selected_test_paths"] == []
    working_dirs = {
        candidate["working_dir"]
        for candidate in payload["candidate_commands"]
        if candidate["command"].startswith("python -m pytest")
    }
    assert {"services/api", "services/worker"} <= working_dirs
    assert "Recommended Working Dir: `services/api`" in markdown
    assert "`services/worker`" in markdown


def test_repository_test_execution_plan_uses_ci_pytest_candidate_when_tox_missing(tmp_path):
    tests_dir = tmp_path / "tests" / "unit"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_sample.py").write_text(
        "def test_smoke():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m tox",
            "test_source_paths": [],
        },
        repository_test_environment={
            "status": "warning",
            "reason": "test_tool_missing",
            "test_module": "tox",
            "test_tool_available": False,
            "ci_configuration": {
                "test_commands": [
                    {
                        "command": "python -m pytest tests/unit && rm -rf out",
                        "reason": "github_actions_test_step",
                    },
                    {
                        "command": "python -m pytest --tb=short tests/unit",
                        "reason": "github_actions_test_step",
                    },
                ],
            },
        },
        repository_root=tmp_path,
    )
    markdown = render_repository_test_execution_plan_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "execution_plan_built"
    assert payload["recommended_execution_command"] == (
        "python -m pytest --tb=short tests/unit"
    )
    assert payload["recommended_execution_level"] == "ci"
    assert payload["recommended_execution_runner"] == "pytest"
    assert payload["recommended_execution_risk"] == "low"
    assert payload["preferred_test_runner"] == "tox"
    assert payload["recommended_execution_source"] == "ci_config"
    assert payload["runner_fallback_used"] is True
    assert payload["runner_fallback_reason"] == "missing_runner:tox"
    assert payload["runner_fallback_from"] == "tox"
    assert payload["runner_fallback_to"] == "pytest"
    assert payload["ci_test_command_candidate_count"] == 1
    assert payload["ci_test_command_candidates"] == [
        "python -m pytest --tb=short tests/unit"
    ]
    assert payload["candidate_commands"][0]["source"] == "ci_config"
    assert payload["candidate_commands"][0]["selected_test_paths"] == ["tests/unit"]
    assert "Runner Fallback Used: true" in markdown
    assert "missing_runner:tox" in markdown
    assert any(
        candidate["level"] == "focused"
        and candidate["command"] == "python -m pytest -q --maxfail=1 tests/unit"
        and candidate["reason"] == "ci_pytest_focused_first_failure"
        for candidate in payload["candidate_commands"]
    )
    assert payload["executable_now"] is True
    assert "CI Test Command Candidates" in markdown
    assert any("CI test command candidates" in action for action in payload["next_actions"])


def test_repository_test_execution_plan_prefers_setup_prepared_runner(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "def test_smoke():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m tox",
            "test_source_paths": [],
            "test_command_candidates": [
                {
                    "rank": 1,
                    "command": "python -m tox",
                    "runner": "tox",
                    "confidence": 0.92,
                    "reason": "tox_ini_detected",
                },
                {
                    "rank": 2,
                    "command": "python -m pytest",
                    "runner": "pytest",
                    "confidence": 0.86,
                    "reason": "pytest_config_or_tests_detected",
                },
            ],
        },
        repository_test_environment={
            "status": "warning",
            "reason": "test_tool_missing",
            "test_module": "tox",
            "test_tool_available": False,
            "ci_configuration": {
                "test_commands": [
                    {
                        "command": "python -m pytest tests",
                        "reason": "tox_test_command",
                    }
                ],
            },
        },
        repository_test_environment_setup={
            "test_module": "tox",
        },
        repository_test_environment_setup_result={
            "status": "pass",
            "executed": True,
            "venv_python": str(tmp_path / ".repo_test_venv" / "bin" / "python"),
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "pass"
    assert payload["reason"] == "execution_plan_built"
    assert payload["recommended_execution_command"] == "python -m tox"
    assert payload["recommended_execution_level"] == "full"
    assert payload["recommended_execution_risk"] == "high"
    assert payload["recommended_execution_runner"] == "tox"
    assert payload["prepared_test_runner"] == "tox"
    assert payload["planned_runner_prepared"] is True
    assert payload["executable_now"] is True
    assert payload["candidate_commands"][0]["runner"] == "tox"
    assert any(
        candidate["runner"] == "pytest" and not candidate["recommended"]
        for candidate in payload["candidate_commands"]
    )


def test_repository_test_execution_plan_normalizes_managed_ci_pytest_candidates(tmp_path):
    for test_dir in (
        tmp_path / "tests" / "unit",
        tmp_path / "tests" / "integration",
        tmp_path / "tests" / "coverage",
        tmp_path / "tests" / "pdm",
        tmp_path / "tests" / "hatch",
        tmp_path / "tests" / "tox",
    ):
        test_dir.mkdir(parents=True)
        (test_dir / "test_sample.py").write_text(
            "def test_smoke():\n"
            "    assert True\n",
            encoding="utf-8",
        )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "",
            "test_source_paths": [],
        },
        repository_test_environment={
            "status": "pass",
            "ci_configuration": {
                "test_commands": [
                    {
                        "command": "uv run --group test pytest --tb=short tests/unit",
                        "reason": "github_actions_test_step",
                    },
                    {
                        "command": "poetry run python -m pytest tests/integration",
                        "reason": "github_actions_test_step",
                    },
                    {
                        "command": "coverage run --source package -m pytest tests/coverage",
                        "reason": "github_actions_test_step",
                    },
                    {
                        "command": "pdm run --dev pytest --maxfail=1 tests/pdm",
                        "reason": "github_actions_test_step",
                    },
                    {
                        "command": "hatch run pytest --tb=short tests/hatch",
                        "reason": "github_actions_test_step",
                    },
                    {
                        "command": "tox -e py311 -- pytest tests/tox",
                        "reason": "github_actions_test_step",
                    },
                    {
                        "command": "uv run pytest tests && rm -rf out",
                        "reason": "github_actions_test_step",
                    },
                ],
            },
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "pass"
    assert payload["recommended_execution_command"] == (
        "python -m pytest --tb=short tests/unit"
    )
    assert payload["recommended_execution_level"] == "ci"
    assert payload["ci_test_command_candidate_count"] == 6
    assert payload["ci_test_command_candidates"] == [
        "python -m pytest --tb=short tests/unit",
        "python -m pytest tests/integration",
        "python -m pytest tests/coverage",
        "python -m pytest --maxfail=1 tests/pdm",
        "python -m pytest --tb=short tests/hatch",
        "python -m pytest tests/tox",
    ]
    assert payload["candidate_commands"][0]["source"] == "ci_config"
    assert payload["candidate_commands"][0]["selected_test_paths"] == ["tests/unit"]
    assert payload["executable_now"] is True


def test_repository_test_execution_plan_prefers_project_script_pytest_target(tmp_path):
    tests_dir = tmp_path / "tests" / "unit"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_sample.py").write_text(
        "def test_smoke():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m pytest",
            "test_source_paths": [],
            "test_command_candidates": [
                {
                    "rank": 1,
                    "command": "python -m pytest",
                    "runner": "pytest",
                    "confidence": 0.5,
                    "reason": "python_project_without_test_files_fallback",
                }
            ],
        },
        repository_test_environment={
            "status": "pass",
            "test_tool_available": True,
            "ci_configuration": {
                "test_commands": [
                    {
                        "command": "python -m pytest --tb=short tests/unit",
                        "runner": "pytest",
                        "reason": "pyproject_test_script",
                        "source": "pyproject.toml:tool.pdm.scripts.test",
                    }
                ],
            },
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "pass"
    assert payload["recommended_execution_command"] == (
        "python -m pytest --tb=short tests/unit"
    )
    assert payload["recommended_execution_level"] == "ci"
    assert payload["recommended_execution_risk"] == "low"
    assert payload["recommended_execution_runner"] == "pytest"
    assert payload["candidate_commands"][0]["source"] == "ci_config"
    assert payload["candidate_commands"][0]["profile_reason"] == (
        "pyproject_test_script"
    )
    assert any(
        candidate["command"] == "python -m pytest -q"
        and not candidate["recommended"]
        for candidate in payload["candidate_commands"]
    )
    assert payload["executable_now"] is True


def test_repository_test_execution_plan_uses_noxfile_pytest_when_nox_missing(tmp_path):
    tests_dir = tmp_path / "tests" / "unit"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_sample.py").write_text(
        "def test_smoke():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m nox",
            "test_source_paths": [],
            "test_command_candidates": [
                {
                    "rank": 1,
                    "command": "python -m nox",
                    "runner": "nox",
                    "confidence": 0.9,
                    "reason": "noxfile_detected",
                }
            ],
        },
        repository_test_environment={
            "status": "warning",
            "reason": "test_tool_missing",
            "test_module": "nox",
            "test_tool_available": False,
            "ci_configuration": {
                "test_commands": [
                    {
                        "command": "python -m pytest --tb=short tests/unit",
                        "runner": "pytest",
                        "reason": "nox_session_run",
                        "source": "noxfile.py:tests",
                    }
                ],
            },
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "pass"
    assert payload["reason"] == "execution_plan_built"
    assert payload["recommended_execution_command"] == (
        "python -m pytest --tb=short tests/unit"
    )
    assert payload["recommended_execution_level"] == "ci"
    assert payload["recommended_execution_runner"] == "pytest"
    assert payload["recommended_execution_risk"] == "low"
    assert payload["preferred_test_runner"] == "nox"
    assert payload["recommended_execution_source"] == "ci_config"
    assert payload["runner_fallback_used"] is True
    assert payload["runner_fallback_reason"] == "missing_runner:nox"
    assert payload["runner_fallback_from"] == "nox"
    assert payload["runner_fallback_to"] == "pytest"
    assert payload["candidate_commands"][0]["source"] == "ci_config"
    assert payload["candidate_commands"][0]["profile_reason"] == "nox_session_run"
    assert any(
        candidate["command"] == "python -m nox"
        and candidate["source"] == "profile_recommended"
        and not candidate["recommended"]
        for candidate in payload["candidate_commands"]
    )
    assert payload["executable_now"] is True


def test_repository_test_execution_plan_uses_configured_pytest_paths_and_addopts(tmp_path):
    tests_dir = tmp_path / "tests" / "unit"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_sample.py").write_text(
        "def test_smoke():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m pytest",
            "test_source_paths": [],
        },
        repository_test_environment={
            "status": "pass",
            "test_tool_available": True,
            "pytest_configuration": {
                "source_count": 1,
                "addopts": ["--tb=short", "--disable-warnings", "-k"],
                "testpaths": ["tests/unit"],
            },
        },
        repository_root=tmp_path,
    )
    markdown = render_repository_test_execution_plan_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["selected_test_paths"] == ["tests/unit"]
    assert payload["configured_test_paths"] == ["tests/unit"]
    assert payload["pytest_addopts"] == ["--tb=short", "--disable-warnings"]
    assert payload["recommended_execution_command"] == (
        "python -m pytest -q --tb=short --disable-warnings tests/unit"
    )
    assert payload["executable_now"] is True
    assert "Configured Test Paths" in markdown


def test_repository_test_execution_plan_carries_framework_environment_variables(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_django_app.py").write_text(
        "def test_smoke():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_execution(
        {
            "recommended_test_command": "python -m pytest",
            "test_source_paths": ["tests/test_django_app.py"],
        },
        repository_test_environment={
            "status": "pass",
            "test_tool_available": True,
            "framework_signals": ["django"],
            "planned_environment_variables": {
                "DJANGO_SETTINGS_MODULE": "mysite.settings",
                "FLASK_APP": "app:app",
                "unsafe-name": "ignored",
            },
        },
        repository_root=tmp_path,
    )
    markdown = render_repository_test_execution_plan_markdown(payload)

    assert payload["planned_environment_variables"] == {
        "DJANGO_SETTINGS_MODULE": "mysite.settings",
        "FLASK_APP": "app:app",
    }
    assert payload["planned_environment_variable_names"] == [
        "DJANGO_SETTINGS_MODULE",
        "FLASK_APP",
    ]
    assert payload["framework_signals"] == ["django"]
    assert any(
        "DJANGO_SETTINGS_MODULE=mysite.settings" in action
        for action in payload["next_actions"]
    )
    assert "Planned Environment Variables" in markdown
