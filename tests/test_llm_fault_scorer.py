from pathlib import Path
import json
import tempfile

from code_intelligence_agent.agents.llm_client import (
    ALIBABA_BEST_JUDGE_MODEL,
    ALIBABA_DASHSCOPE_CHAT_COMPLETIONS_URL,
    DEEPSEEK_BEST_JUDGE_MODEL,
    DEEPSEEK_CHAT_COMPLETIONS_URL,
    LOCALIZATION_SYSTEM_PROMPT,
    StaticLLMClient,
    create_alibaba_localization_client,
    create_localization_client,
)
from code_intelligence_agent.agents.llm_fault_scorer import (
    LLMFaultScorer,
    parse_llm_function_scores,
)
from code_intelligence_agent.core.call_graph import build_call_graph
from code_intelligence_agent.core.fault_localizer import (
    FaultLocalizationConfig,
    FaultLocalizer,
)
from code_intelligence_agent.core.models import TestExecutionSummary
from code_intelligence_agent.core.program_graph import build_program_graph
from code_intelligence_agent.core.repo_parser import RepoParser


def test_parse_llm_function_scores_accepts_code_fence_and_clamps_scores():
    parsed = parse_llm_function_scores(
        "```json\n"
        + json.dumps(
            {
                "scores": [
                    {"function_id": "f1", "score": 1.2, "reason": "high"},
                    {"function_id": "f2", "score": -0.2, "reason": "low"},
                    {"id": "f3", "score": "0.55"},
                ]
            }
        )
        + "\n```"
    )

    assert [item.function_id for item in parsed] == ["f1", "f2", "f3"]
    assert [item.score for item in parsed] == [1.0, 0.0, 0.55]
    assert parsed[0].reason == "high"


def test_alibaba_localization_client_uses_dedicated_env(monkeypatch):
    monkeypatch.setenv("CIA_LOCALIZATION_LLM_API_KEY", "fake-key")
    monkeypatch.delenv("CIA_LOCALIZATION_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CIA_LOCALIZATION_LLM_MODEL", raising=False)
    monkeypatch.delenv("CIA_LOCALIZATION_LLM_BASE_URL", raising=False)

    client = create_alibaba_localization_client()

    assert client.provider == "alibaba"
    assert client.api_key == "fake-key"
    assert client.model == ALIBABA_BEST_JUDGE_MODEL
    assert client.base_url == ALIBABA_DASHSCOPE_CHAT_COMPLETIONS_URL
    assert client.system_prompt == LOCALIZATION_SYSTEM_PROMPT


def test_localization_client_defaults_to_deepseek_and_judge_key(monkeypatch):
    monkeypatch.delenv("CIA_LOCALIZATION_LLM_API_KEY", raising=False)
    monkeypatch.setenv("CIA_JUDGE_API_KEY", "fake-judge-key")
    monkeypatch.delenv("CIA_LOCALIZATION_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CIA_LOCALIZATION_LLM_MODEL", raising=False)
    monkeypatch.delenv("CIA_LOCALIZATION_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    client = create_localization_client()

    assert client.provider == "deepseek"
    assert client.api_key == "fake-judge-key"
    assert client.model == DEEPSEEK_BEST_JUDGE_MODEL
    assert client.base_url == DEEPSEEK_CHAT_COMPLETIONS_URL
    assert client.system_prompt == LOCALIZATION_SYSTEM_PROMPT


def test_fault_localizer_uses_llm_score_signal_from_static_client():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def buggy(value):\n"
            "    return value + 1\n\n"
            "def clean(value):\n"
            "    return value * 2\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import buggy, clean\n\n"
            "def test_buggy():\n"
            "    assert buggy(1) == 3\n\n"
            "def test_clean():\n"
            "    assert clean(2) == 4\n",
            encoding="utf-8",
        )
        parsed = RepoParser().parse(repo)
        call_graph = build_call_graph(parsed.functions, parsed.calls)
        program_graph = build_program_graph(parsed, call_graph)
        by_name = {
            function.metadata["qualified_name"]: function for function in parsed.functions
        }
        buggy = by_name["buggy"]
        clean = by_name["clean"]
        test_buggy = by_name["test_buggy"]
        client = StaticLLMClient(
            json.dumps(
                {
                    "scores": [
                        {"function_id": buggy.id, "score": 0.9, "reason": "matches"},
                        {"function_id": clean.id, "score": 0.1, "reason": "unrelated"},
                    ]
                }
            )
        )
        summary = TestExecutionSummary(
            failed_tests={test_buggy.id},
            coverage={test_buggy.id: set()},
            test_names={test_buggy.id: "test_buggy"},
            failure_messages={test_buggy.id: "AssertionError in buggy increment path"},
        )

        with_llm = FaultLocalizer(
            llm_scorer=LLMFaultScorer(client)
        ).rank(program_graph, [], summary)
        without_llm = FaultLocalizer(
            FaultLocalizationConfig(use_llm_score=False),
            llm_scorer=LLMFaultScorer(client),
        ).rank(program_graph, [], summary)
        target = next(item for item in with_llm if item.function_name == "buggy")
        ablated_target = next(item for item in without_llm if item.function_name == "buggy")

    assert target.signals["llm"] == 0.9
    assert ablated_target.signals["llm"] == 0.0
    assert target.score > ablated_target.score
    assert "source_excerpt" in client.prompts[0]
    assert "AssertionError in buggy increment path" in client.prompts[0]
