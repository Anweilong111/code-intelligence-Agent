from __future__ import annotations

import json

from code_intelligence_agent import main as cli_module
from code_intelligence_agent.evaluation import github_repo_intelligence
from code_intelligence_agent.evaluation import v3_localization_evaluation
from code_intelligence_agent.evaluation import v3_repair_evaluation
from code_intelligence_agent.evaluation import v3_semantic_evaluation
from code_intelligence_agent.evaluation import v4_experiment_protocol
from code_intelligence_agent.evaluation import v4_real_bug_benchmark


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


def test_top_level_cli_routes_v3_repair_evaluation(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_v3_repair_evaluation_main(argv):
        captured["argv"] = list(argv)

    monkeypatch.setattr(
        v3_repair_evaluation,
        "main",
        fake_v3_repair_evaluation_main,
    )

    cli_module.main(
        [
            "v3-repair-eval",
            "outputs_v3/example",
            "--strategies",
            "rule",
        ]
    )

    assert captured["argv"] == [
        "outputs_v3/example",
        "--strategies",
        "rule",
    ]


def test_top_level_cli_routes_v3_localization_evaluation(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_v3_localization_evaluation_main(argv):
        captured["argv"] = list(argv)

    monkeypatch.setattr(
        v3_localization_evaluation,
        "main",
        fake_v3_localization_evaluation_main,
    )

    cli_module.main(
        [
            "v3-localization-eval",
            "outputs_v3/localization",
            "--no-runtime-coverage",
        ]
    )

    assert captured["argv"] == [
        "outputs_v3/localization",
        "--no-runtime-coverage",
    ]


def test_top_level_cli_routes_v3_semantic_evaluation(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_v3_semantic_evaluation_main(argv):
        captured["argv"] = list(argv)

    monkeypatch.setattr(
        v3_semantic_evaluation,
        "main",
        fake_v3_semantic_evaluation_main,
    )

    cli_module.main(
        [
            "v3-semantic-eval",
            "outputs_v3/semantic",
            "--case-id",
            "bugsinpy-pysnooper-3",
        ]
    )

    assert captured["argv"] == [
        "outputs_v3/semantic",
        "--case-id",
        "bugsinpy-pysnooper-3",
    ]


def test_top_level_cli_routes_v4_protocol_audit(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_v4_protocol_audit_main(argv):
        captured["argv"] = list(argv)

    monkeypatch.setattr(
        v4_experiment_protocol,
        "main",
        fake_v4_protocol_audit_main,
    )

    cli_module.main(
        [
            "v4-protocol-audit",
            "datasets/v4_agent_effectiveness/experiment_protocol.json",
            "outputs_v4/phase0",
            "--require-pass",
        ]
    )

    assert captured["argv"] == [
        "datasets/v4_agent_effectiveness/experiment_protocol.json",
        "outputs_v4/phase0",
        "--require-pass",
    ]


def test_top_level_cli_routes_v4_real_bug_benchmark(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_v4_real_bug_benchmark_main(argv):
        captured["argv"] = list(argv)

    monkeypatch.setattr(
        v4_real_bug_benchmark,
        "main",
        fake_v4_real_bug_benchmark_main,
    )

    cli_module.main(
        [
            "v4-benchmark-catalog",
            "audit",
            "datasets/v4_agent_effectiveness/real_bug_seed_catalog.json",
            "--format",
            "json",
        ]
    )

    assert captured["argv"] == [
        "audit",
        "datasets/v4_agent_effectiveness/real_bug_seed_catalog.json",
        "--format",
        "json",
    ]
