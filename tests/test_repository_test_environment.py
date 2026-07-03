from pathlib import Path

from code_intelligence_agent.evaluation import repository_test_environment
from code_intelligence_agent.evaluation.repository_test_environment import (
    plan_repository_test_environment,
    render_repository_test_environment_markdown,
    write_repository_test_environment_artifacts,
)


def test_repository_test_environment_recommends_requirements_install(monkeypatch, tmp_path):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: object() if module == "pytest" else None,
    )
    (tmp_path / "requirements-test.txt").write_text("pytest\n", encoding="utf-8")
    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m pytest",
            "project_config_files": ["requirements-test.txt", "pyproject.toml"],
        },
        repository_root=tmp_path,
    )
    paths = write_repository_test_environment_artifacts(payload, tmp_path / "out")
    markdown = render_repository_test_environment_markdown(payload)

    assert payload["status"] == "warning"
    assert payload["reason"] == "config_files_missing_in_checkout"
    assert payload["recommended_install_command"] == (
        "python -m pip install -r requirements-test.txt"
    )
    assert payload["install_command_reason"] == "requirements-test.txt"
    assert payload["test_module"] == "pytest"
    assert payload["test_tool_available"] is True
    assert payload["missing_config_files"] == ["pyproject.toml"]
    assert "Repository Test Environment Plan" in markdown
    assert Path(paths["repository_test_environment_json"]).exists()
    assert Path(paths["repository_test_environment_markdown"]).exists()


def test_repository_test_environment_warns_when_test_tool_missing(monkeypatch):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: None,
    )
    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m tox",
            "project_config_files": ["tox.ini"],
        }
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "test_tool_missing"
    assert payload["recommended_install_command"] == "python -m pip install tox"
    assert payload["test_module"] == "tox"
    assert payload["test_tool_available"] is False


def test_repository_test_environment_ignores_nested_example_setup_files(monkeypatch):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: object(),
    )
    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m pytest",
            "project_config_files": ["examples/demo/setup.py"],
        }
    )

    assert payload["status"] == "pass"
    assert payload["recommended_install_command"] == ""
    assert payload["install_command_reason"] == "no_dependency_manifest"
    assert payload["dependency_files"] == []


def test_repository_test_environment_detects_uv_project(monkeypatch, tmp_path):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: object() if module == "pytest" else None,
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")

    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m pytest",
            "project_config_files": ["pyproject.toml", "uv.lock"],
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "pass"
    assert payload["recommended_install_command"] == "uv sync --dev"
    assert payload["install_command_reason"] == "uv_lock"
    assert payload["dependency_files"] == ["pyproject.toml", "uv.lock"]


def test_repository_test_environment_extracts_pytest_config_and_ci_env(monkeypatch, tmp_path):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: object() if module == "pytest" else None,
    )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        "addopts = '--tb=short --disable-warnings -k unsafe'\n"
        "testpaths = ['tests/unit', '../unsafe', 'tests/integration']\n"
        "env = ['DJANGO_SETTINGS_MODULE=mysite.settings', 'BAD=$(rm -rf /)']\n",
        encoding="utf-8",
    )
    (workflows / "ci.yml").write_text(
        "name: ci\n"
        "env:\n"
        "  FLASK_APP: app:app\n"
        "  UNSAFE: ${{ secrets.VALUE }}\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m pytest",
            "project_config_files": [
                ".github/workflows/ci.yml",
                "pyproject.toml",
            ],
        },
        repository_root=tmp_path,
    )
    markdown = render_repository_test_environment_markdown(payload)

    assert payload["pytest_config_source_count"] == 2
    assert payload["pytest_config_addopts"] == ["--tb=short", "--disable-warnings"]
    assert payload["pytest_config_testpaths"] == [
        "tests/unit",
        "tests/integration",
    ]
    assert payload["planned_environment_variables"] == {
        "DJANGO_SETTINGS_MODULE": "mysite.settings",
        "FLASK_APP": "app:app",
    }
    assert "Pytest Configuration" in markdown
    assert "--tb=short --disable-warnings" in markdown


def test_repository_test_environment_extracts_ci_and_tox_test_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: object() if module == "pytest" else None,
    )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (tmp_path / "requirements-test.txt").write_text("pytest\n", encoding="utf-8")
    (workflows / "ci.yml").write_text(
        "name: ci\n"
        "jobs:\n"
        "  test:\n"
        "    strategy:\n"
        "      matrix:\n"
        "        python-version: ['3.10', '3.11']\n"
        "    steps:\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: ${{ matrix.python-version }}\n"
        "      - run: python -m pip install -r requirements-test.txt\n"
        "      - run: python -m pytest --tb=short tests\n",
        encoding="utf-8",
    )
    (tmp_path / "tox.ini").write_text(
        "[tox]\n"
        "envlist = py310, py311\n"
        "\n"
        "[testenv]\n"
        "deps =\n"
        "    -r requirements-test.txt\n"
        "commands =\n"
        "    python -m pytest --tb=short tests\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m pytest",
            "project_config_files": [
                ".github/workflows/ci.yml",
                "tox.ini",
            ],
        },
        repository_root=tmp_path,
    )
    markdown = render_repository_test_environment_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["recommended_install_command"] == (
        "python -m pip install -r requirements-test.txt"
    )
    assert payload["install_command_reason"] == "ci_install_candidate"
    assert payload["ci_config_source_count"] == 2
    assert payload["ci_python_versions"] == ["3.10", "3.11"]
    assert "python -m pip install -r requirements-test.txt" in payload[
        "ci_install_command_candidates"
    ]
    assert "python -m pytest --tb=short tests" in payload[
        "ci_test_command_candidates"
    ]
    assert payload["tox_envlist"] == ["py310", "py311"]
    assert payload["ci_configuration"]["setup_python_detected"] is True
    assert "CI Test Configuration" in markdown
    assert "CI Install Command Candidates" in markdown
    assert any("CI test command candidates" in action for action in payload["next_actions"])


def test_repository_test_environment_normalizes_managed_ci_pytest_commands(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: object() if module == "pytest" else None,
    )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "name: ci\n"
        "jobs:\n"
        "  test:\n"
        "    steps:\n"
        "      - run: uv run --group test pytest --tb=short tests/unit\n"
        "      - run: poetry run python -m pytest tests/integration\n"
        "      - run: coverage run --source package -m pytest tests/coverage\n"
        "      - run: pdm run --dev pytest --maxfail=1 tests/pdm\n"
        "      - run: hatch run pytest --tb=short tests/hatch\n"
        "      - run: hatch run -e test +py=3.11 pytest tests/hatch-env\n"
        "      - run: tox -e py311 -- pytest --tb=short tests/tox\n"
        "      - run: python -m tox -e py310 -- python -m pytest tests/tox-python\n"
        "      - run: uv run pytest tests && rm -rf out\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m pytest",
            "project_config_files": [".github/workflows/ci.yml"],
        },
        repository_root=tmp_path,
    )

    assert payload["ci_test_command_candidates"] == [
        "python -m pytest --tb=short tests/unit",
        "python -m pytest tests/integration",
        "python -m pytest tests/coverage",
        "python -m pytest --maxfail=1 tests/pdm",
        "python -m pytest --tb=short tests/hatch",
        "python -m pytest tests/hatch-env",
        "python -m pytest --tb=short tests/tox",
        "python -m pytest tests/tox-python",
    ]
    assert payload["ci_configuration"]["test_command_count"] == 8
    assert all(
        command["runner"] == "pytest"
        for command in payload["ci_configuration"]["test_commands"]
    )


def test_repository_test_environment_extracts_pyproject_test_scripts(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: object() if module == "pytest" else None,
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pdm.scripts]\n"
        "test = 'pytest --tb=short tests/unit'\n"
        "integration = {cmd = 'python -m pytest tests/integration'}\n"
        "lint = 'ruff check .'\n"
        "\n"
        "[tool.hatch.envs.default.scripts]\n"
        "test = 'pytest --maxfail=1 tests/hatch'\n"
        "\n"
        "[tool.poetry.scripts]\n"
        "pytest_entry = 'pytest tests/poetry'\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m pytest",
            "project_config_files": ["pyproject.toml"],
        },
        repository_root=tmp_path,
    )
    markdown = render_repository_test_environment_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["ci_config_source_count"] == 1
    assert payload["ci_test_command_candidates"] == [
        "python -m pytest --tb=short tests/unit",
        "python -m pytest tests/integration",
        "python -m pytest --maxfail=1 tests/hatch",
        "python -m pytest tests/poetry",
    ]
    assert payload["ci_configuration"]["test_command_count"] == 4
    assert all(
        command["reason"] == "pyproject_test_script"
        for command in payload["ci_configuration"]["test_commands"]
    )
    assert payload["ci_configuration"]["test_commands"][0]["source"] == (
        "pyproject.toml:tool.pdm.scripts.test"
    )
    assert "pyproject_test_script" in markdown


def test_repository_test_environment_extracts_nox_session_commands(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: object() if module == "pytest" else None,
    )
    (tmp_path / "noxfile.py").write_text(
        "import nox\n\n"
        "@nox.session(python=['3.10', '3.11'])\n"
        "def tests(session):\n"
        "    session.install('-r', 'requirements-test.txt')\n"
        "    session.install('pytest', 'pytest-cov')\n"
        "    session.run('pytest', '--tb=short', 'tests/unit')\n"
        "    target = 'tests/unsafe'\n"
        "    session.run('pytest', target)\n"
        "\n"
        "@nox.session\n"
        "def coverage(session):\n"
        "    session.run('coverage', 'run', '-m', 'pytest', 'tests/coverage')\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m nox",
            "project_config_files": ["noxfile.py"],
        },
        repository_root=tmp_path,
    )
    markdown = render_repository_test_environment_markdown(payload)

    assert payload["status"] == "warning"
    assert payload["reason"] == "test_tool_missing"
    assert payload["recommended_install_command"] == "python -m pip install nox"
    assert payload["ci_python_versions"] == ["3.10", "3.11"]
    assert payload["ci_install_command_candidates"] == [
        "python -m pip install -r requirements-test.txt",
        "python -m pip install pytest pytest-cov",
    ]
    assert payload["ci_test_command_candidates"] == [
        "python -m pytest --tb=short tests/unit",
        "python -m pytest tests/coverage",
    ]
    assert payload["ci_configuration"]["test_command_count"] == 2
    assert payload["ci_configuration"]["test_commands"][0]["source"] == (
        "noxfile.py:tests"
    )
    assert all(
        command["reason"] == "nox_session_run"
        for command in payload["ci_configuration"]["test_commands"]
    )
    assert "nox_session_run" in markdown


def test_repository_test_environment_plans_django_settings_module(monkeypatch, tmp_path):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: object() if module == "pytest" else None,
    )
    (tmp_path / "requirements-test.txt").write_text(
        "pytest\npytest-django\nDjango>=4\n",
        encoding="utf-8",
    )
    (tmp_path / "manage.py").write_text(
        "import os\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m pytest",
            "project_config_files": ["requirements-test.txt"],
            "framework_signals": ["django"],
            "framework_profile": {
                "frameworks": ["django"],
                "django_settings_candidates": [
                    {
                        "module": "mysite.settings",
                        "source_path": "mysite/settings.py",
                        "confidence": 0.86,
                        "reason": "settings_py",
                    }
                ],
            },
        },
        repository_root=tmp_path,
    )
    markdown = render_repository_test_environment_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["framework_signals"] == ["django"]
    assert payload["planned_environment_variables"] == {
        "DJANGO_SETTINGS_MODULE": "mysite.settings"
    }
    assert payload["framework_test_configuration"]["reason"] == (
        "django_settings_module_detected"
    )
    assert any("DJANGO_SETTINGS_MODULE=mysite.settings" in action for action in payload["next_actions"])
    assert "Framework Test Configuration" in markdown


def test_repository_test_environment_detects_fastapi_app_bootstrap(monkeypatch, tmp_path):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: object() if module == "pytest" else None,
    )
    (tmp_path / "requirements-test.txt").write_text(
        "pytest\nfastapi\nhttpx\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text(
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_service.py").write_text(
        "from fastapi.testclient import TestClient\n\n"
        "def test_smoke():\n"
        "    assert TestClient\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m pytest",
            "project_config_files": ["requirements-test.txt"],
        },
        repository_root=tmp_path,
    )
    markdown = render_repository_test_environment_markdown(payload)

    framework_config = payload["framework_test_configuration"]
    assert payload["status"] == "pass"
    assert payload["framework_signals"] == ["fastapi"]
    assert framework_config["reason"] == "fastapi_app_bootstrap_detected"
    assert framework_config["app_bootstrap_candidates"][0]["app_import"] == (
        "service:app"
    )
    assert framework_config["test_bootstrap_signals"][0]["signal"] == (
        "fastapi_testclient_detected"
    )
    assert any("FastAPI app bootstrap candidate" in action for action in payload["next_actions"])
    assert "App Bootstrap Candidates" in markdown


def test_repository_test_environment_plans_flask_app_env(monkeypatch, tmp_path):
    monkeypatch.setattr(
        repository_test_environment.importlib.util,
        "find_spec",
        lambda module: object() if module == "pytest" else None,
    )
    (tmp_path / "requirements-test.txt").write_text(
        "pytest\nflask\n",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        "from flask import Flask\n\n"
        "app = Flask(__name__)\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        "def test_client(app):\n"
        "    assert app.test_client()\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "python -m pytest",
            "project_config_files": ["requirements-test.txt"],
        },
        repository_root=tmp_path,
    )

    framework_config = payload["framework_test_configuration"]
    assert payload["status"] == "pass"
    assert payload["framework_signals"] == ["flask"]
    assert framework_config["reason"] == "flask_app_bootstrap_detected"
    assert payload["planned_environment_variables"] == {"FLASK_APP": "app:app"}
    assert framework_config["app_bootstrap_candidates"][0]["app_import"] == "app:app"
    assert framework_config["test_bootstrap_signals"][0]["signal"] == (
        "flask_test_client_detected"
    )


def test_repository_test_environment_skips_without_test_command():
    payload = plan_repository_test_environment(
        {
            "recommended_test_command": "",
            "project_config_files": ["pyproject.toml"],
        }
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "no_recommended_test_command"
