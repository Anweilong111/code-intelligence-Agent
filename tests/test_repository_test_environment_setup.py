from pathlib import Path
import subprocess

from code_intelligence_agent.evaluation.repository_test_environment_setup import (
    execute_repository_test_environment_setup,
    plan_repository_test_environment_setup,
    render_repository_test_environment_setup_result_markdown,
    render_repository_test_environment_setup_markdown,
    write_repository_test_environment_setup_artifacts,
    write_repository_test_environment_setup_result_artifacts,
)


def test_repository_test_environment_setup_rewrites_pip_install_to_venv(tmp_path):
    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install tox",
            "install_command_reason": "tox_runner",
            "test_module": "tox",
            "test_tool_available": False,
        },
        output_dir=tmp_path / "out",
    )
    paths = write_repository_test_environment_setup_artifacts(
        payload,
        tmp_path / "out",
    )
    markdown = render_repository_test_environment_setup_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "environment_setup_plan_built"
    assert payload["isolation_mode"] == "venv"
    assert payload["install_command_supported"] is True
    assert payload["install_requires_repository_root"] is False
    assert payload["install_command_args"][1:4] == ["-m", "pip", "install"]
    assert payload["install_command_args"][-1] == "tox"
    assert "Repository Test Environment Setup Plan" in markdown
    assert Path(paths["repository_test_environment_setup_json"]).exists()
    assert Path(paths["repository_test_environment_setup_markdown"]).exists()


def test_repository_test_environment_setup_installs_additional_ci_runners(tmp_path):
    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install tox",
            "install_command_reason": "tox_runner",
            "test_module": "tox",
            "test_tool_available": False,
            "ci_configuration": {
                "test_commands": [
                    {
                        "command": "python -m pytest tests",
                        "runner": "pytest",
                        "reason": "tox_test_command",
                    }
                ],
            },
        },
        output_dir=tmp_path / "out",
    )
    markdown = render_repository_test_environment_setup_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["install_command_augmented"] is True
    assert payload["additional_test_runner_modules"] == ["pytest"]
    assert payload["install_command_args"][-2:] == ["tox", "pytest"]
    assert "Additional Test Runner Modules: pytest" in markdown


def test_repository_test_environment_setup_adds_project_install_for_runner_only_command(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install tox",
            "install_command_reason": "tox_runner",
            "test_module": "tox",
            "test_tool_available": False,
            "dependency_files": ["pyproject.toml"],
            "ci_configuration": {
                "test_commands": [
                    {
                        "command": "python -m pytest tests",
                        "runner": "pytest",
                        "reason": "tox_test_command",
                    }
                ],
            },
        },
        output_dir=tmp_path / "out",
        repository_root=repo,
    )
    markdown = render_repository_test_environment_setup_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["install_command_augmented"] is True
    assert payload["additional_test_runner_modules"] == ["pytest"]
    assert payload["project_install_augmented"] is True
    assert payload["project_install_augmentation_reason"] == (
        "runner_install_with_project_metadata"
    )
    assert payload["project_install_metadata_files"] == ["pyproject.toml"]
    assert payload["install_requires_repository_root"] is True
    assert payload["repository_root_present"] is True
    assert payload["install_command_args"][-3:] == ["pytest", "-e", "."]
    assert "Project Install Augmented: true" in markdown
    assert "`pyproject.toml`" in markdown


def test_repository_test_environment_setup_records_pytest_plugin_candidates_from_requirement_file(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (repo / "requirements-dev.txt").write_text(
        "pytest>=2.8.0,<=6.2.5\n"
        "pytest-cov\n"
        "pytest-httpbin==2.0.0\n"
        "pytest-mock==2.0.0\n"
        "requests-mock>=1.11\n"
        "requests\n"
        "-e .[socks]\n"
        "git+https://example.invalid/pkg.git#egg=pytest-unsafe\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install tox",
            "install_command_reason": "tox_runner",
            "test_module": "tox",
            "test_tool_available": False,
            "dependency_files": ["pyproject.toml", "requirements-dev.txt"],
            "ci_configuration": {
                "test_commands": [
                    {
                        "command": "python -m pytest tests",
                        "runner": "pytest",
                        "reason": "tox_test_command",
                    }
                ],
            },
        },
        output_dir=tmp_path / "out",
        repository_root=repo,
    )
    markdown = render_repository_test_environment_setup_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["install_command_augmented"] is True
    assert payload["pytest_plugin_dependencies_augmented"] is False
    assert payload["pytest_plugin_dependency_specs"] == []
    assert payload["pytest_plugin_dependency_sources"] == []
    assert payload["pytest_plugin_dependency_candidates"] == [
        "pytest-cov",
        "pytest-httpbin==2.0.0",
        "pytest-mock==2.0.0",
        "requests-mock>=1.11",
    ]
    assert payload["pytest_plugin_dependency_candidate_sources"] == [
        {
            "source": "requirements-dev.txt",
            "requirement": "pytest-cov",
            "package": "pytest-cov",
        },
        {
            "source": "requirements-dev.txt",
            "requirement": "pytest-httpbin==2.0.0",
            "package": "pytest-httpbin",
        },
        {
            "source": "requirements-dev.txt",
            "requirement": "pytest-mock==2.0.0",
            "package": "pytest-mock",
        },
        {
            "source": "requirements-dev.txt",
            "requirement": "requests-mock>=1.11",
            "package": "requests-mock",
        },
    ]
    assert "pytest>=2.8.0,<=6.2.5" not in payload["install_command_args"]
    assert "pytest-cov" not in payload["install_command_args"]
    assert "pytest-httpbin==2.0.0" not in payload["install_command_args"]
    assert "pytest-mock==2.0.0" not in payload["install_command_args"]
    assert "requests" not in payload["install_command_args"]
    assert "git+https://example.invalid/pkg.git" not in payload["install_command_args"]
    assert payload["install_command_args"][-2:] == ["-e", "."]
    assert "Pytest Plugin Dependencies Augmented: false" in markdown
    assert "Pytest Plugin Dependency Candidates" in markdown
    assert "`pytest-mock==2.0.0` from `requirements-dev.txt`" in markdown


def test_repository_test_environment_setup_skips_pytest_plugin_scan_without_root(
    tmp_path,
):
    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install tox",
            "install_command_reason": "tox_runner",
            "test_module": "tox",
            "test_tool_available": False,
            "dependency_files": ["requirements-dev.txt"],
        },
        output_dir=tmp_path / "out",
    )

    assert payload["status"] == "pass"
    assert payload["pytest_plugin_dependencies_augmented"] is False
    assert payload["pytest_plugin_dependency_specs"] == []
    assert payload["pytest_plugin_dependency_candidates"] == []
    assert payload["install_command_args"][-1] == "tox"


def test_repository_test_environment_setup_requires_root_for_project_install_augmentation(
    tmp_path,
):
    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install tox",
            "install_command_reason": "tox_runner",
            "test_module": "tox",
            "test_tool_available": False,
            "dependency_files": ["setup.cfg"],
        },
        output_dir=tmp_path / "out",
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "repository_root_missing_for_install"
    assert payload["project_install_augmented"] is True
    assert payload["project_install_metadata_files"] == ["setup.cfg"]
    assert payload["install_requires_repository_root"] is True


def test_repository_test_environment_setup_requires_root_for_requirements(tmp_path):
    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": (
                "python -m pip install -r requirements-test.txt"
            ),
            "install_command_reason": "requirements-test.txt",
            "dependency_files": ["requirements-test.txt"],
        },
        output_dir=tmp_path / "out",
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "repository_root_missing_for_install"
    assert payload["install_command_supported"] is True
    assert payload["install_requires_repository_root"] is True


def test_repository_test_environment_setup_accepts_root_relative_install(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install -e .",
            "install_command_reason": "editable_project",
            "dependency_files": ["pyproject.toml"],
        },
        output_dir=tmp_path / "out",
        repository_root=repo,
    )

    assert payload["status"] == "pass"
    assert payload["install_command_supported"] is True
    assert payload["install_requires_repository_root"] is True
    assert payload["repository_root_present"] is True


def test_repository_test_environment_setup_warns_when_root_config_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install -e .",
            "install_command_reason": "editable_project",
            "dependency_files": ["pyproject.toml"],
            "missing_config_files": ["pyproject.toml"],
        },
        output_dir=tmp_path / "out",
        repository_root=repo,
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "config_files_missing_in_checkout"
    assert payload["install_command_supported"] is True
    assert payload["missing_config_files"] == ["pyproject.toml"]


def test_repository_test_environment_setup_translates_managed_project_installs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    for command, reason, translation_reason in (
        ("uv sync --dev", "uv_lock", "uv_sync_to_pip_editable"),
        ("pdm install -d", "pdm_project", "pdm_install_to_pip_editable"),
        (
            "poetry install --with dev",
            "poetry_lock",
            "poetry_install_to_pip_editable",
        ),
        ("hatch env create", "hatch_project", "hatch_env_to_pip_editable"),
        ("pipenv install --dev", "pipfile", "pipenv_install_to_pip_editable"),
    ):
        payload = plan_repository_test_environment_setup(
            {
                "recommended_install_command": command,
                "install_command_reason": reason,
                "dependency_files": ["pyproject.toml"],
            },
            output_dir=tmp_path / "out",
            repository_root=repo,
        )

        assert payload["status"] == "pass"
        assert payload["reason"] == "environment_setup_plan_built"
        assert payload["install_command_supported"] is True
        assert payload["install_requires_repository_root"] is True
        assert payload["install_command_translation_reason"] == translation_reason
        assert payload["install_command_args"][1:4] == ["-m", "pip", "install"]
        assert payload["install_command_args"][-2:] == ["-e", "."]


def test_repository_test_environment_setup_requires_root_for_managed_project_install(
    tmp_path,
):
    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "uv sync --dev",
            "install_command_reason": "uv_lock",
            "dependency_files": ["pyproject.toml", "uv.lock"],
        },
        output_dir=tmp_path / "out",
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "repository_root_missing_for_install"
    assert payload["install_command_supported"] is True
    assert payload["install_command_translation_reason"] == "uv_sync_to_pip_editable"


def test_repository_test_environment_setup_warns_for_unsupported_install(tmp_path):
    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "npm install",
            "install_command_reason": "unsupported_manager",
        },
        output_dir=tmp_path / "out",
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "unsupported_install_command"
    assert payload["install_command_supported"] is False
    assert payload["install_command_args"] == []


def test_repository_test_environment_setup_skips_without_install_command(tmp_path):
    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "",
            "install_command_reason": "no_dependency_manifest",
        },
        output_dir=tmp_path / "out",
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "no_install_command"
    assert payload["install_command_supported"] is False
    assert payload["repository_dependency_install_requested"] is False


def test_repository_test_environment_setup_execution_skips_by_default(tmp_path):
    plan = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install tox",
            "install_command_reason": "tox_runner",
        },
        output_dir=tmp_path / "out",
    )

    payload = execute_repository_test_environment_setup(plan)

    assert payload["status"] == "skipped"
    assert payload["executed"] is False
    assert payload["reason"] == "execution_disabled"


def test_repository_test_environment_setup_execution_runs_create_and_install(tmp_path):
    calls = []

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del capture_output, text, timeout, check, env
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, "ok", "")

    plan = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install tox",
            "install_command_reason": "tox_runner",
        },
        output_dir=tmp_path / "out",
    )

    payload = execute_repository_test_environment_setup(
        plan,
        enabled=True,
        timeout=5,
        runner=fake_runner,
    )
    paths = write_repository_test_environment_setup_result_artifacts(
        payload,
        tmp_path / "out",
    )
    markdown = render_repository_test_environment_setup_result_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["executed"] is True
    assert payload["create_executed"] is True
    assert payload["install_executed"] is True
    assert payload["create_returncode"] == 0
    assert payload["install_returncode"] == 0
    assert len(calls) == 2
    assert calls[0][1] is None
    assert calls[1][1] is None
    assert "Repository Test Environment Setup Result" in markdown
    assert Path(paths["repository_test_environment_setup_result_json"]).exists()
    assert Path(paths["repository_test_environment_setup_result_markdown"]).exists()


def test_repository_test_environment_setup_runner_probe_installs_only_pytest(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['setuptools']\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install -e .[test]",
            "install_command_reason": "editable_project_with_test_extra",
            "dependency_files": ["pyproject.toml"],
            "dependency_access_blockers": ["credential_reference:PRIVATE_INDEX_TOKEN"],
            "test_module": "tox",
            "test_tool_available": False,
            "repository_compatibility": {
                "install_policy": {
                    "risk": "high",
                    "reasons": ["repository-local build hook"],
                    "requires_explicit_authorization": True,
                    "auto_execution_allowed": False,
                }
            },
        },
        output_dir=tmp_path / "out",
        repository_root=repo,
        setup_mode="runner-probe",
    )
    markdown = render_repository_test_environment_setup_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "runner_probe_setup_plan_built"
    assert payload["setup_mode"] == "runner_probe"
    assert payload["test_module"] == "pytest"
    assert payload["install_command_args"][-1] == "pytest"
    assert "-e" not in payload["install_command_args"]
    assert ".[test]" not in payload["install_command_args"]
    assert payload["repository_code_install_requested"] is False
    assert payload["repository_dependency_install_requested"] is False
    assert payload["install_requires_repository_root"] is False
    assert payload["install_risk"] == "low"
    assert payload["install_requires_explicit_authorization"] is False
    assert payload["project_install_augmented"] is False
    assert "Runner Probe" not in markdown
    assert "runner_probe" in markdown
    assert "Repository Code Install Requested: false" in markdown


def test_repository_test_environment_setup_rejects_unknown_mode(tmp_path):
    try:
        plan_repository_test_environment_setup(
            {"recommended_install_command": "python -m pip install pytest"},
            output_dir=tmp_path / "out",
            setup_mode="unbounded",
        )
    except ValueError as exc:
        assert "unsupported repository test environment setup mode" in str(exc)
    else:
        raise AssertionError("unknown setup mode must be rejected")


def test_repository_test_environment_setup_runner_probe_execution_is_auditable(
    tmp_path,
):
    calls = []

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del capture_output, text, timeout, check, env
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, "ok", "")

    plan = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install -e .",
            "test_module": "tox",
        },
        output_dir=tmp_path / "out",
        setup_mode="runner_probe",
    )

    payload = execute_repository_test_environment_setup(
        plan,
        enabled=True,
        runner=fake_runner,
    )

    assert payload["status"] == "pass"
    assert payload["setup_mode"] == "runner_probe"
    assert payload["test_module"] == "pytest"
    assert payload["repository_code_install_requested"] is False
    assert payload["repository_dependency_install_requested"] is False
    assert payload["install_command_args"][-1] == "pytest"
    assert len(calls) == 2
    assert calls[1][1] is None


def test_repository_test_environment_setup_execution_records_install_failure(tmp_path):
    calls = []

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del cwd, capture_output, text, timeout, check, env
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0 if len(calls) == 1 else 1,
            "stdout",
            "stderr",
        )

    plan = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install tox",
            "install_command_reason": "tox_runner",
        },
        output_dir=tmp_path / "out",
    )

    payload = execute_repository_test_environment_setup(
        plan,
        enabled=True,
        runner=fake_runner,
    )

    assert payload["status"] == "fail"
    assert payload["reason"] == "dependency_install_failed"
    assert payload["create_returncode"] == 0
    assert payload["install_returncode"] == 1
    assert payload["install_failure_category"] == "unknown_install_failure"
    assert payload["install_fallback_executed"] is False


def test_repository_test_environment_setup_falls_back_from_editable_install(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = []

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del capture_output, text, timeout, check, env
        calls.append((command, cwd))
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 0, "venv", "")
        if len(calls) == 2:
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                "ERROR: Project backend does not support editable installs",
            )
        return subprocess.CompletedProcess(command, 0, "installed", "")

    plan = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install -e .",
            "install_command_reason": "editable_project",
            "dependency_files": ["pyproject.toml"],
        },
        output_dir=tmp_path / "out",
        repository_root=repo,
    )

    payload = execute_repository_test_environment_setup(
        plan,
        enabled=True,
        runner=fake_runner,
    )
    markdown = render_repository_test_environment_setup_result_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "environment_setup_executed_with_install_fallback"
    assert payload["install_returncode"] == 1
    assert payload["install_failure_category"] == "editable_backend_unsupported"
    assert payload["install_fallback_executed"] is True
    assert payload["install_fallback_returncode"] == 0
    assert payload["install_fallback_command_args"][-1] == "."
    assert "-e" not in payload["install_fallback_command_args"]
    assert len(calls) == 3
    assert calls[1][1] == repo
    assert calls[2][1] == repo
    assert "Install Fallback Return Code" in markdown
    assert "editable_backend_unsupported" in markdown


def test_repository_test_environment_setup_classifies_missing_requirement_file(
    tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = []

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del cwd, capture_output, text, timeout, check, env
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 0, "venv", "")
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            "ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements-test.txt'",
        )

    plan = plan_repository_test_environment_setup(
        {
            "recommended_install_command": (
                "python -m pip install -r requirements-test.txt"
            ),
            "install_command_reason": "requirements-test.txt",
            "dependency_files": ["requirements-test.txt"],
        },
        output_dir=tmp_path / "out",
        repository_root=repo,
    )

    payload = execute_repository_test_environment_setup(
        plan,
        enabled=True,
        runner=fake_runner,
    )

    assert payload["status"] == "fail"
    assert payload["reason"] == "dependency_install_failed"
    assert payload["install_failure_category"] == "missing_requirement_file"
    assert "requirements file" in payload["install_failure_signal"]
    assert payload["install_fallback_executed"] is False
    assert any("checkout ref/depth" in action for action in payload["next_actions"])
