import json
from pathlib import Path

from code_intelligence_agent.evaluation.github_onboarding_matrix import (
    build_github_onboarding_matrix,
    render_github_onboarding_matrix_markdown,
    write_github_onboarding_matrix_artifacts,
)


def test_github_onboarding_matrix_passes_when_ten_repos_cover_p6_scenarios(tmp_path):
    cases = [
        _write_case(
            tmp_path,
            "pytest_repo",
            config_files=["pytest.ini"],
            test_count=2,
            runners=["pytest"],
        ),
        _write_case(
            tmp_path,
            "src_layout_repo",
            config_files=["pyproject.toml"],
            test_count=1,
            runners=["pytest"],
            src_layout_packages=["src/demo"],
        ),
        _write_case(
            tmp_path,
            "pyproject_repo",
            config_files=["pyproject.toml"],
            test_count=1,
            runners=["pytest"],
        ),
        _write_case(
            tmp_path,
            "requirements_repo",
            config_files=["requirements.txt"],
            test_count=1,
            runners=["unittest"],
        ),
        _write_case(
            tmp_path,
            "tox_repo",
            config_files=["tox.ini"],
            test_count=1,
            runners=["tox"],
        ),
        _write_case(
            tmp_path,
            "no_python_repo",
            imported_sources=0,
            analyzed_files=0,
            config_files=[],
            test_count=0,
            runners=[],
        ),
        _write_case(
            tmp_path,
            "no_tests_repo",
            config_files=["pyproject.toml"],
            test_count=0,
            runners=[],
        ),
        _write_case(
            tmp_path,
            "missing_dependency_repo",
            config_files=["requirements-dev.txt"],
            test_count=1,
            runners=["pytest"],
            environment_reason="missing_dependency:pytest",
            recommended_install_command="python -m pip install -r requirements-dev.txt",
        ),
        _write_case(
            tmp_path,
            "timeout_repo",
            config_files=["pytest.ini"],
            test_count=1,
            runners=["pytest"],
            execution_failure_category="timeout",
        ),
        _write_case(
            tmp_path,
            "failing_test_repo",
            config_files=["pytest.ini"],
            test_count=1,
            runners=["pytest"],
            execution_failure_category="test_assertion_failure",
            execution_failure_signal="FAILED tests/test_bug.py::test_bug",
        ),
    ]

    matrix = build_github_onboarding_matrix(cases)
    markdown = render_github_onboarding_matrix_markdown(matrix)
    paths = write_github_onboarding_matrix_artifacts(matrix, tmp_path / "matrix")

    assert matrix["status"] == "pass"
    assert matrix["case_count"] == 10
    assert matrix["passed_check_count"] == matrix["check_count"]
    assert all(
        row["scenario_tags"]
        for row in matrix["rows"]
    )
    for scenario_id in (
        "pytest_project",
        "src_layout_project",
        "pyproject_project",
        "requirements_project",
        "tox_or_nox_project",
        "no_python_sources",
        "no_tests",
        "dependency_missing",
        "timeout",
        "failing_test_evidence",
    ):
        assert matrix["scenario_coverage"][scenario_id]["count"] >= 1
    assert all(not row["missing_required_artifacts"] for row in matrix["rows"])
    assert all(row["policy_trace"]["present"] is True for row in matrix["rows"])
    assert "GitHub Onboarding Matrix" in markdown
    assert Path(paths["github_onboarding_matrix_json"]).exists()
    assert Path(paths["github_onboarding_matrix_markdown"]).exists()


def test_github_onboarding_matrix_marks_missing_policy_trace_incomplete(tmp_path):
    report = _write_case(
        tmp_path,
        "pytest_repo",
        config_files=["pytest.ini"],
        test_count=1,
        runners=["pytest"],
    )
    (Path(report).parent / "agent_policy_trace.json").unlink()

    matrix = build_github_onboarding_matrix([report], required_case_count=1)
    row = matrix["rows"][0]

    assert matrix["status"] == "incomplete"
    assert row["status"] == "incomplete"
    assert "agent_policy_trace.json" in row["missing_required_artifacts"]
    assert row["policy_trace"]["present"] is False
    artifact_check = {
        check["name"]: check for check in matrix["checks"]
    }["required_artifacts_per_repository"]
    assert artifact_check["passed"] is False


def _write_case(
    root: Path,
    name: str,
    *,
    imported_sources: int = 3,
    analyzed_files: int = 3,
    config_files: list[str],
    test_count: int,
    runners: list[str],
    src_layout_packages: list[str] | None = None,
    environment_reason: str = "",
    recommended_install_command: str = "",
    execution_failure_category: str = "",
    execution_failure_signal: str = "",
) -> Path:
    case_dir = root / name
    case_dir.mkdir()
    repo = f"example/{name}"
    candidates = [
        {
            "rank": index + 1,
            "runner": runner,
            "command": _command_for_runner(runner),
            "confidence": 0.9,
            "reason": f"{runner}_signal",
            "evidence": [runner],
        }
        for index, runner in enumerate(runners)
    ]
    test_paths = [
        f"tests/test_{index}.py"
        for index in range(test_count)
    ]
    profile = {
        "imported_source_count": imported_sources,
        "python_source_ratio": 1.0 if imported_sources else 0.0,
        "test_source_count": test_count,
        "test_source_paths": test_paths,
        "package_roots": ["demo"] if imported_sources else [],
        "src_layout_packages": src_layout_packages or [],
        "project_config_files": config_files,
        "test_framework_signals": runners,
        "test_command_candidates": candidates,
        "recommended_test_command": candidates[0]["command"] if candidates else "",
    }
    structure = {
        "analyzed_file_count": analyzed_files,
        "package_structure": {
            "package_roots": profile["package_roots"],
            "src_layout_packages": src_layout_packages or [],
            "recommended_target_prefix": (
                (src_layout_packages or [""])[0] if src_layout_packages else ""
            ),
        },
        "project_config": {
            "project_config_files": config_files,
            "dependency_tool_signals": [
                Path(item).stem for item in config_files
            ],
        },
        "test_structure": {
            "test_source_count": test_count,
            "test_source_paths": test_paths,
            "test_directories": ["tests"] if test_count else [],
            "test_framework_signals": runners,
            "recommended_test_command": candidates[0]["command"] if candidates else "",
            "test_command_candidates": candidates,
            "test_command_candidate_count": len(candidates),
        },
    }
    discovery = {
        "status": "pass" if test_count else "blocked",
        "reason": "test_sources_discovered" if test_count else "no_tests_discovered",
        "blocker": "" if test_count else "oracle:no_tests",
        "test_source_count": test_count,
        "test_source_paths": test_paths,
        "test_directories": ["tests"] if test_count else [],
        "test_framework_signals": runners,
        "recommended_test_command": candidates[0]["command"] if candidates else "",
        "test_command_candidates": candidates,
        "project_config_files": config_files,
    }
    environment = {
        "status": "warning" if environment_reason else "pass",
        "reason": environment_reason or "test_environment_ready",
        "recommended_install_command": recommended_install_command,
    }
    execution_plan = {
        "command": candidates[0]["command"] if candidates else "",
        "runner": runners[0] if runners else "",
        "candidate_commands": candidates,
    }
    execution_result = {
        "status": "fail" if execution_failure_category else "skipped",
        "failure_category": execution_failure_category,
        "failure_signal": execution_failure_signal,
    }
    policy_trace = {
        "status": "pass",
        "selected_action": {"id": "discover_tests"},
        "canonical_action": {"id": "discover_tests"},
        "loop": ["observe", "plan", "act", "verify", "reflect", "replan"],
    }
    _write_json(case_dir / "repository_profile.json", profile)
    _write_json(case_dir / "repository_structure.json", structure)
    _write_json(case_dir / "repository_test_discovery.json", discovery)
    _write_json(case_dir / "repository_test_environment.json", environment)
    _write_json(case_dir / "repository_test_execution_plan.json", execution_plan)
    _write_json(case_dir / "repository_test_execution_result.json", execution_result)
    _write_json(case_dir / "agent_policy_trace.json", policy_trace)
    for filename in (
        "repository_profile.md",
        "repository_structure.md",
        "repository_test_discovery.md",
        "repository_test_environment.md",
        "repository_test_execution_plan.md",
        "agent_policy_trace.md",
    ):
        (case_dir / filename).write_text(f"# {filename}\n", encoding="utf-8")
    report = {
        "repo": repo,
        "repo_spec": repo,
        "output_dir": str(case_dir),
        "status": "pass",
        "passed": True,
        "repository_test_dynamic_usable_for_localization": (
            execution_failure_category == "test_assertion_failure"
        ),
    }
    _write_json(case_dir / "github_repo_intelligence.json", report)
    return case_dir / "github_repo_intelligence.json"


def _command_for_runner(runner: str) -> str:
    if runner == "unittest":
        return "python -m unittest discover"
    return f"python -m {runner}"


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
