from pathlib import Path
import subprocess

from code_intelligence_agent.evaluation.repository_compatibility import (
    assess_repository_compatibility,
    render_repository_compatibility_markdown,
    write_repository_compatibility_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_environment_setup import (
    execute_repository_test_environment_setup,
    plan_repository_test_environment_setup,
)


def _profile(*config_files: str) -> dict:
    return {
        "scope_status": "supported",
        "scope_reason": "python_sources_discovered_and_imported",
        "discovered_python_source_count": 8,
        "imported_source_count": 2,
        "layout_type": "src_layout",
        "source_roots": ["src/demo"],
        "test_roots": ["tests"],
        "recommended_analysis_roots": ["src/demo"],
        "recommended_test_command": "python -m pytest",
        "test_source_count": 3,
        "test_framework_signals": ["pytest"],
        "project_config_files": list(config_files),
        "dependency_manager_profile": {
            "tool_signals": ["pyproject"],
            "dependency_files": list(config_files),
        },
    }


def test_repository_compatibility_reports_ready_declarative_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\n"
        "requires = ['setuptools']\n"
        "build-backend = 'setuptools.build_meta'\n"
        "[project]\n"
        "name = 'demo'\n"
        "requires-python = '>=3.10,<3.13'\n",
        encoding="utf-8",
    )

    payload = assess_repository_compatibility(
        _profile("pyproject.toml"),
        repository_root=tmp_path,
        current_python="3.11.8",
    )
    markdown = render_repository_compatibility_markdown(payload)
    paths = write_repository_compatibility_artifacts(payload, tmp_path / "out")

    assert payload["status"] == "ready"
    assert payload["scope"]["analysis_available"] is True
    assert payload["python"]["status"] == "compatible"
    assert payload["install_policy"]["risk"] == "medium"
    assert payload["install_policy"]["build_backend_allowlisted"] is True
    assert payload["install_policy"]["auto_execution_allowed"] is True
    assert payload["layout"]["source_roots"] == ["src/demo"]
    assert payload["layout"]["test_roots"] == ["tests"]
    assert "Repository Compatibility Assessment" in markdown
    assert Path(paths["repository_compatibility_json"]).is_file()
    assert Path(paths["repository_compatibility_markdown"]).is_file()


def test_repository_compatibility_blocks_incompatible_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'legacy'\nrequires-python = '<3.10'\n",
        encoding="utf-8",
    )

    payload = assess_repository_compatibility(
        _profile("pyproject.toml"),
        repository_root=tmp_path,
        current_python="3.11.4",
    )

    assert payload["status"] == "blocked"
    assert payload["termination_reason"] == "environment:python_version_incompatible"
    assert payload["python"]["status"] == "incompatible"
    assert payload["install_policy"]["auto_execution_allowed"] is False


def test_repository_compatibility_blocks_private_dependency_without_exposing_secret(
    tmp_path,
):
    secret = "super-secret-value"
    (tmp_path / "requirements.txt").write_text(
        f"--extra-index-url https://user:{secret}@private.example/simple\n"
        "demo-package==1.0\n",
        encoding="utf-8",
    )
    profile = _profile("requirements.txt")

    payload = assess_repository_compatibility(
        profile,
        repository_root=tmp_path,
        current_python="3.11",
    )
    rendered = render_repository_compatibility_markdown(payload)

    assert payload["status"] == "blocked"
    assert payload["primary_blocker"] == "dependency:private_index"
    assert payload["dependency_access"]["blockers"] == ["private_index"]
    assert secret not in rendered
    assert secret not in str(payload)


def test_repository_compatibility_marks_legacy_setup_as_high_risk(tmp_path):
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\nsetup(name='legacy')\n",
        encoding="utf-8",
    )
    profile = _profile("setup.py")

    payload = assess_repository_compatibility(
        profile,
        repository_root=tmp_path,
        current_python="3.11",
    )

    assert payload["status"] == "partial"
    assert payload["termination_reason"] == (
        "safety:high_risk_install_requires_authorization"
    )
    assert payload["install_policy"]["risk"] == "high"
    assert payload["install_policy"]["requires_explicit_authorization"] is True
    assert payload["install_policy"]["auto_execution_allowed"] is False


def test_repository_compatibility_returns_unsupported_scope():
    payload = assess_repository_compatibility(
        {
            "scope_status": "unsupported_scope",
            "scope_reason": "no_python_sources_discovered",
            "scope_blocker": "unsupported_scope",
            "discovered_python_source_count": 0,
            "imported_source_count": 0,
            "layout_type": "no_python_source",
        }
    )

    assert payload["status"] == "blocked"
    assert payload["termination_reason"] == "unsupported_scope"
    assert payload["primary_blocker"] == "unsupported_scope"
    assert payload["test_execution"]["readiness"] == "unsupported"


def test_repository_compatibility_distinguishes_config_snapshot_from_checkout(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nrequires-python = '>=3.10'\n",
        encoding="utf-8",
    )

    payload = assess_repository_compatibility(
        _profile("pyproject.toml"),
        repository_root=tmp_path,
        repository_execution_root="",
        current_python="3.11",
    )

    assert payload["repository_root_present"] is True
    assert payload["repository_execution_root_present"] is False
    assert payload["status"] == "partial"
    assert payload["termination_reason"] == "checkout:repository_root_not_materialized"
    assert payload["test_execution"]["readiness"] == "checkout_required"


def test_high_risk_setup_is_not_executed_without_explicit_authorization(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "setup.py").write_text("raise RuntimeError('must not run')\n", encoding="utf-8")
    environment = {
        "recommended_install_command": "python -m pip install -e .",
        "install_command_reason": "editable_project",
        "dependency_files": ["setup.py"],
        "project_config_files": ["setup.py"],
    }
    plan = plan_repository_test_environment_setup(
        environment,
        output_dir=tmp_path / "out",
        repository_root=repo,
    )
    calls = []

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "ok", "")

    result = execute_repository_test_environment_setup(
        plan,
        enabled=True,
        runner=fake_runner,
    )

    assert plan["status"] == "warning"
    assert plan["reason"] == "high_risk_install_requires_authorization"
    assert plan["install_risk"] == "high"
    assert result["status"] == "skipped"
    assert result["reason"] == "high_risk_install_requires_authorization"
    assert calls == []


def test_high_risk_setup_can_run_only_with_explicit_authorization(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")
    plan = plan_repository_test_environment_setup(
        {
            "recommended_install_command": "python -m pip install -e .",
            "install_command_reason": "editable_project",
            "dependency_files": ["setup.py"],
            "project_config_files": ["setup.py"],
        },
        output_dir=tmp_path / "out",
        repository_root=repo,
        allow_high_risk_install=True,
    )
    calls = []

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "ok", "")

    result = execute_repository_test_environment_setup(
        plan,
        enabled=True,
        runner=fake_runner,
        allow_high_risk_install=True,
    )

    assert plan["status"] == "pass"
    assert plan["high_risk_install_authorized"] is True
    assert result["status"] == "pass"
    assert len(calls) == 2
