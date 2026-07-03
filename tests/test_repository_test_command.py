import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.repository_test_command import (
    render_repository_test_command_markdown,
    validate_repository_test_command,
)


def test_repository_test_command_skips_without_full_repo_root():
    payload = validate_repository_test_command(
        {"recommended_test_command": "python -m pytest"}
    )
    markdown = render_repository_test_command_markdown(payload)

    assert payload["status"] == "skipped"
    assert payload["executed"] is False
    assert payload["reason"] == "full_repo_not_materialized"
    assert "full repository checkout" in payload["message"]
    assert "Repository Test Command Validation" in markdown


def test_repository_test_command_executes_safe_python_module_command():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "test_sample.py").write_text(
            "def test_smoke():\n"
            "    assert True\n",
            encoding="utf-8",
        )

        payload = validate_repository_test_command(
            {"recommended_test_command": "python -m pytest -q"},
            repository_root=repo,
            timeout=10,
        )

        assert payload["status"] == "pass"
        assert payload["executed"] is True
        assert payload["returncode"] == 0
        assert payload["passed"] == 1
        assert payload["failed"] == 0
        assert payload["command_args"][1:3] == ["-m", "pytest"]


def test_repository_test_command_rejects_unsupported_shell_command():
    with tempfile.TemporaryDirectory() as tmp_dir:
        payload = validate_repository_test_command(
            {"recommended_test_command": "pytest -q"},
            repository_root=tmp_dir,
        )

        assert payload["status"] == "skipped"
        assert payload["reason"] == "unsupported_command"
        assert payload["executed"] is False
