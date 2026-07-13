from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import (
    FaultLocalizer,
    evidence_v2_localization_config,
)
from code_intelligence_agent.core.git_change_history import GitChangeHistoryAnalyzer
from code_intelligence_agent.core.models import TestExecutionSummary
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser


def test_evidence_v2_separates_real_dynamic_evidence_and_reconstructs_score(
    tmp_path,
):
    (tmp_path / "sample.py").write_text(
        "def target(value):\n"
        "    if value:\n"
        "        return value + 1\n"
        "    return 0\n\n"
        "def other(value):\n"
        "    return value\n\n"
        "def test_target():\n"
        "    assert target(1) == 1\n",
        encoding="utf-8",
    )
    graph = _graph(tmp_path)
    by_name = {
        function.metadata.get("qualified_name", function.name): function
        for function in graph.functions.values()
    }
    target = by_name["target"]
    test_target = by_name["test_target"]
    localizer = FaultLocalizer(evidence_v2_localization_config())

    unproven = localizer.rank(
        graph,
        [],
        TestExecutionSummary(
            failed_tests={test_target.id},
            coverage={test_target.id: {target.id}},
        ),
    )
    unproven_target = next(item for item in unproven if item.function_id == target.id)
    assert unproven_target.signals["test_failure"] == 0.0
    assert unproven_target.signals["test_failure_available"] == 0.0

    proven = localizer.rank(
        graph,
        [],
        TestExecutionSummary(
            failed_tests={test_target.id},
            coverage={test_target.id: {target.id}},
            dynamic_evidence_test_ids={test_target.id},
        ),
    )
    proven_target = next(item for item in proven if item.function_id == target.id)
    assert proven_target.signals["test_failure"] == 1.0
    assert proven_target.signals["test_failure_available"] == 1.0
    contributions = [
        value
        for name, value in proven_target.signals.items()
        if name.startswith("contribution_")
        and name != "contribution_clamp_adjustment"
    ]
    reconstructed = (
        sum(contributions)
        + proven_target.signals["contribution_clamp_adjustment"]
    )
    assert reconstructed == pytest.approx(proven_target.score, abs=1e-4)


def test_evidence_v2_bounds_stacktrace_propagation(tmp_path):
    (tmp_path / "sample.py").write_text(
        "def first():\n"
        "    return second()\n\n"
        "def second():\n"
        "    return third()\n\n"
        "def third():\n"
        "    return fourth()\n\n"
        "def fourth():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    graph = _graph(tmp_path)
    by_name = {
        function.metadata.get("qualified_name", function.name): function
        for function in graph.functions.values()
    }
    config = evidence_v2_localization_config()
    config = config.__class__(
        **{
            **config.__dict__,
            "graph_propagation_max_depth": 2,
            "graph_propagation_decay": 0.5,
        }
    )
    ranked = FaultLocalizer(config).rank(
        graph,
        [],
        TestExecutionSummary(
            traceback_function_ids={by_name["first"].id},
            dynamic_traceback_function_ids={by_name["first"].id},
        ),
    )
    signals = {item.function_name: item.signals for item in ranked}
    assert signals["first"]["traceback"] == 1.0
    assert signals["second"]["traceback"] == 1.0
    assert signals["third"]["traceback"] == 0.5
    assert signals["fourth"]["traceback"] == 0.0


def test_evidence_v2_llm_cannot_rank_without_program_evidence(tmp_path):
    (tmp_path / "sample.py").write_text(
        "def first():\n"
        "    return 1\n\n"
        "def second():\n"
        "    return 2\n",
        encoding="utf-8",
    )
    graph = _graph(tmp_path)

    class Scorer:
        def score(self, **kwargs):
            return {
                function_id: 1.0
                for function_id in kwargs["candidate_function_ids"]
            }

    ranked = FaultLocalizer(
        evidence_v2_localization_config(),
        llm_scorer=Scorer(),
    ).rank(graph, [], TestExecutionSummary())
    assert all(item.signals["llm_raw"] == 1.0 for item in ranked)
    assert all(item.signals["llm"] == 0.0 for item in ranked)
    assert all(item.signals["contribution_llm"] == 0.0 for item in ranked)


def test_git_history_analyzer_reports_missing_repository(tmp_path):
    result = GitChangeHistoryAnalyzer().analyze(tmp_path / "missing", [])
    assert result.status == "skipped"
    assert result.reason == "not_a_git_repository"
    assert result.scores == {}


@pytest.mark.skipif(shutil.which("git") is None, reason="git is unavailable")
def test_git_history_analyzer_scores_function_line_history(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "tests@example.com")
    _git(tmp_path, "config", "user.name", "CIA Tests")
    source = tmp_path / "sample.py"
    source.write_text(
        "def stable():\n"
        "    return 1\n\n"
        "def hot(value):\n"
        "    if value:\n"
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "sample.py")
    _git(
        tmp_path,
        "commit",
        "-m",
        "initial",
        env=_git_date_env("2024-01-01T00:00:00+00:00"),
    )
    source.write_text(
        source.read_text(encoding="utf-8").replace("return 1\n    return 0", "return 2\n    return 0"),
        encoding="utf-8",
    )
    _git(tmp_path, "add", "sample.py")
    _git(
        tmp_path,
        "commit",
        "-m",
        "change hot function",
        env=_git_date_env("2025-01-01T00:00:00+00:00"),
    )
    parsed = RepoParser().parse(tmp_path)
    result = GitChangeHistoryAnalyzer().analyze(
        tmp_path,
        parsed.functions,
        now_timestamp=datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp(),
    )
    by_name = {
        function.metadata.get("qualified_name", function.name): function
        for function in parsed.functions
    }
    assert result.status == "available"
    assert result.commit
    assert result.scores[by_name["hot"].id] > result.scores[by_name["stable"].id]
    hot_evidence = result.function_evidence[by_name["hot"].id]
    assert hot_evidence["unique_last_change_commits"] == 2
    assert hot_evidence["score"] == result.scores[by_name["hot"].id]


def _graph(root):
    parsed = RepoParser().parse(root)
    call_graph = build_call_graph(parsed.functions, parsed.calls, parsed.imports)
    return build_program_graph(parsed, call_graph)


def _git(root, *arguments, env=None):
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr


def _git_date_env(value):
    return {
        **os.environ,
        "GIT_AUTHOR_DATE": value,
        "GIT_COMMITTER_DATE": value,
    }
