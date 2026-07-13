from __future__ import annotations

import json
from pathlib import Path

from code_intelligence_agent.agents.intent_parser import SUPPORTED_INTENTS
from code_intelligence_agent.agents.intent_router import route_user_intent


DATASET_PATH = (
    Path(__file__).parents[1]
    / "datasets"
    / "intent_routing"
    / "v2_intent_routing_112.json"
)


def _utterances() -> list[dict[str, str]]:
    payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    rows = []
    for group in payload["groups"]:
        for message in group["messages"]:
            rows.append(
                {
                    "message": message,
                    "expected_intent": group["expected_intent"],
                    "category": group["category"],
                }
            )
    assert payload["utterance_count"] == len(rows)
    return rows


def test_bilingual_intent_dataset_has_required_scale_and_coverage():
    rows = _utterances()
    covered = {row["expected_intent"] for row in rows}

    assert len(rows) >= 100
    assert covered == set(SUPPORTED_INTENTS)
    assert any(any("\u4e00" <= char <= "\u9fff" for char in row["message"]) for row in rows)
    assert any(row["message"].isascii() and row["message"] for row in rows)
    assert {
        "negation_and_constraints",
        "ambiguous_multi_intent_irrelevant",
        "high_risk",
    }.issubset({row["category"] for row in rows})


def test_rule_fallback_accuracy_is_at_least_ninety_percent():
    rows = _utterances()
    results = [
        (
            row,
            route_user_intent(row["message"], llm_enabled=False)["intent"],
        )
        for row in rows
    ]
    correct = sum(actual == row["expected_intent"] for row, actual in results)
    accuracy = correct / len(results)
    failures = [
        {
            "message": row["message"],
            "expected": row["expected_intent"],
            "actual": actual,
        }
        for row, actual in results
        if actual != row["expected_intent"]
    ]

    assert accuracy >= 0.90, json.dumps(failures, ensure_ascii=False, indent=2)
