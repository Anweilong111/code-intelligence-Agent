import subprocess
import sys
from pathlib import Path

from code_intelligence_agent.evaluation.repository_test_timeout_narrowing import (
    execute_repository_test_timeout_narrowing,
    plan_repository_test_timeout_narrowing,
    render_repository_test_timeout_narrowing_markdown,
    write_repository_test_timeout_narrowing_artifacts,
)


def test_timeout_narrowing_expands_selected_directory_to_test_files(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    (tests_dir / "test_b.py").write_text("def test_b(): pass\n", encoding="utf-8")
    (tests_dir / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")

    payload = plan_repository_test_timeout_narrowing(
        {
            "candidate_commands": [
                {
                    "command": "python -m pytest -q --maxfail=1 tests",
                    "selected_test_paths": ["tests"],
                }
            ]
        },
        _timeout_retry_result(),
        repository_root=tmp_path,
        max_attempts=1,
    )
    paths = write_repository_test_timeout_narrowing_artifacts(
        payload,
        tmp_path / "out",
    )
    markdown = render_repository_test_timeout_narrowing_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "timeout_narrowing_plan_built"
    assert payload["narrow_test_files"] == ["tests/test_a.py"]
    assert payload["attempts"][0]["command"] == (
        "python -m pytest -q --maxfail=1 tests/test_a.py"
    )
    assert "Repository Test Timeout Narrowing" in markdown
    assert Path(paths["repository_test_timeout_narrowing_json"]).exists()
    assert Path(paths["repository_test_timeout_narrowing_markdown"]).exists()


def test_timeout_narrowing_expands_selected_file_to_test_nodeids(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_slow.py").write_text(
        "\n".join(
            [
                "def helper():",
                "    return 1",
                "",
                "def test_first():",
                "    assert True",
                "",
                "class TestSlow:",
                "    def test_second(self):",
                "        assert True",
                "",
                "class Helper:",
                "    def test_ignored(self):",
                "        assert True",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = plan_repository_test_timeout_narrowing(
        {
            "selected_test_paths": ["tests/test_slow.py"],
        },
        {
            **_timeout_retry_result(),
            "command": "python -m pytest -q --maxfail=1 tests/test_slow.py",
            "retry_command": "python -m pytest -q --maxfail=1 tests/test_slow.py",
        },
        repository_root=tmp_path,
        max_attempts=2,
    )

    assert payload["status"] == "pass"
    assert payload["selected_test_targets"] == ["tests/test_slow.py"]
    assert payload["narrow_test_files"] == ["tests/test_slow.py"]
    assert payload["narrow_test_targets"] == [
        "tests/test_slow.py::test_first",
        "tests/test_slow.py::TestSlow::test_second",
    ]
    assert payload["attempts"][0]["granularity"] == "nodeid"
    assert payload["attempts"][0]["command"] == (
        "python -m pytest -q --maxfail=1 tests/test_slow.py::test_first"
    )


def test_timeout_narrowing_preserves_command_nodeid_target(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_slow.py").write_text(
        "def test_first():\n    assert True\n",
        encoding="utf-8",
    )

    payload = plan_repository_test_timeout_narrowing(
        {},
        {
            **_timeout_retry_result(),
            "command": "python -m pytest -q tests/test_slow.py::test_first",
            "retry_command": "python -m pytest -q tests/test_slow.py::test_first",
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "pass"
    assert payload["selected_test_paths"] == ["tests/test_slow.py"]
    assert payload["selected_test_targets"] == ["tests/test_slow.py::test_first"]
    assert payload["narrow_test_targets"] == ["tests/test_slow.py::test_first"]
    assert payload["attempts"][0]["path"] == "tests/test_slow.py"
    assert payload["attempts"][0]["target"] == "tests/test_slow.py::test_first"


def test_timeout_narrowing_skips_non_timeout_result(tmp_path):
    payload = plan_repository_test_timeout_narrowing(
        {},
        {
            "status": "fail",
            "executed": True,
            "command": "python -m pytest -q tests",
            "failure_category": "missing_dependency",
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "no_timeout_retry"
    assert payload["safe_to_execute"] is False


def test_timeout_narrowing_executes_until_non_timeout_result(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    (tests_dir / "test_b.py").write_text("def test_b(): pass\n", encoding="utf-8")
    commands = []

    def fake_runner(command, cwd, capture_output, text, timeout, check, env):
        del cwd, capture_output, text, timeout, check, env
        commands.append(command)
        if len(commands) == 1:
            raise subprocess.TimeoutExpired(command, 3, output="", stderr="")
        return subprocess.CompletedProcess(
            command,
            1,
            "FAILED tests/test_b.py::test_b - AssertionError",
            "",
        )

    plan = plan_repository_test_timeout_narrowing(
        {
            "selected_test_paths": ["tests"],
        },
        _timeout_retry_result(),
        repository_root=tmp_path,
        max_attempts=2,
    )
    payload = execute_repository_test_timeout_narrowing(
        plan,
        enabled=True,
        timeout=3,
        python_executable=sys.executable,
        runner=fake_runner,
    )

    assert payload["executed"] is True
    assert payload["reason"] == "timeout_narrowing_selected_non_timeout_result"
    assert payload["attempt_count"] == 2
    assert payload["selected_failure_category"] == "test_assertion_failure"
    assert payload["selected_execution"]["timeout_narrowing_path"] == (
        "tests/test_b.py"
    )
    assert commands[0][:3] == [sys.executable, "-m", "pytest"]
    assert commands[1][-1] == "tests/test_b.py"


def _timeout_retry_result():
    return {
        "status": "fail",
        "executed": True,
        "command": "python -m pytest -q --maxfail=1 tests",
        "retry_command": "python -m pytest -q --maxfail=1 tests",
        "failure_category": "timeout",
        "failure_signal": "timeout>20s",
    }
