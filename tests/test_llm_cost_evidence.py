import json

import pytest

from code_intelligence_agent.evaluation.llm_cost_evidence import (
    build_llm_cost_evidence,
    main,
    render_llm_cost_evidence_markdown,
)


def test_llm_cost_evidence_computes_configured_cost_and_dedupes_response_id():
    source = {
        "candidate": {
            "metadata": {
                "llm_metadata": {
                    "model": "deepseek-v4-pro",
                    "raw": {
                        "id": "response-1",
                        "model": "deepseek-v4-pro",
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 20,
                            "total_tokens": 30,
                        },
                    },
                }
            }
        },
        "duplicate": {
            "id": "response-1",
            "model": "deepseek-v4-pro",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        },
    }

    payload = build_llm_cost_evidence(
        [source],
        source_paths=["patch_candidates.json"],
        input_usd_per_1k_tokens=0.01,
        output_usd_per_1k_tokens=0.02,
    )
    markdown = render_llm_cost_evidence_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["summary"]["usage_record_count"] == 1
    assert payload["llm_token_cost"]["total_tokens"] == 30
    assert payload["llm_token_cost"]["prompt_tokens"] == 10
    assert payload["llm_token_cost"]["completion_tokens"] == 20
    assert payload["llm_token_cost"]["total_estimated_cost"] == 0.0005
    assert payload["llm_token_cost"]["cost_case_count"] == 1
    assert "LLM Cost Evidence" in markdown
    assert "deepseek-v4-pro" in markdown


def test_llm_cost_evidence_requires_pricing_for_cost():
    payload = build_llm_cost_evidence(
        [
            {
                "raw": {
                    "id": "response-1",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                }
            }
        ]
    )

    assert payload["status"] == "incomplete"
    assert payload["reason"] == "llm_pricing_missing"
    assert payload["llm_token_cost"]["token_case_count"] == 1
    assert payload["llm_token_cost"]["cost_case_count"] == 0


def test_llm_cost_evidence_cli_writes_artifacts(tmp_path, capsys):
    source_path = tmp_path / "source.json"
    output_dir = tmp_path / "out"
    source_path.write_text(
        json.dumps(
            {
                "raw": {
                    "id": "response-1",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5},
                }
            }
        ),
        encoding="utf-8",
    )

    main(
        [
            str(output_dir),
            str(source_path),
            "--input-usd-per-1k-tokens",
            "0.01",
            "--output-usd-per-1k-tokens",
            "0.02",
            "--format",
            "markdown",
            "--require-pass",
        ]
    )
    stdout = capsys.readouterr().out

    assert "LLM Cost Evidence" in stdout
    assert (output_dir / "llm_cost_evidence.json").exists()
    assert (output_dir / "llm_cost_evidence.md").exists()

    with pytest.raises(SystemExit):
        main([str(output_dir), str(source_path), "--require-pass"])
