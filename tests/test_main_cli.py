from __future__ import annotations

import json

from code_intelligence_agent import main as cli_module
from code_intelligence_agent.evaluation import github_repo_intelligence


def test_top_level_cli_keeps_local_static_analysis(tmp_path, capsys):
    source = tmp_path / "sample.py"
    source.write_text(
        "def normalize(value):\n"
        "    if value is None:\n"
        "        return 0\n"
        "    return value\n",
        encoding="utf-8",
    )

    cli_module.main([str(source)])

    payload = json.loads(capsys.readouterr().out)
    assert len(payload["repo"]["files"]) == 1
    assert len(payload["repo"]["functions"]) == 1
    assert len(payload["call_graph"]["nodes"]) >= 1
    assert len(payload["program_graph"]["nodes"]) >= 1


def test_top_level_cli_routes_agent_shortcut(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_repo_agent_main(argv):
        captured["argv"] = list(argv)

    monkeypatch.setattr(github_repo_intelligence, "main", fake_repo_agent_main)

    cli_module.main(
        [
            "https://github.com/example/project",
            "--agent",
            "--format",
            "json",
        ]
    )

    assert captured["argv"] == [
        "https://github.com/example/project",
        "--agent",
        "--format",
        "json",
    ]


def test_top_level_cli_agent_subcommand_enables_agent_mode(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_repo_agent_main(argv):
        captured["argv"] = list(argv)

    monkeypatch.setattr(github_repo_intelligence, "main", fake_repo_agent_main)

    cli_module.main(["agent", "example/project", "--format", "markdown"])

    assert captured["argv"] == [
        "example/project",
        "--format",
        "markdown",
        "--agent",
    ]
