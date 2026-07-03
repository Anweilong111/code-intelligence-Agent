import json
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.llm_repair_showcase_matrix import (
    build_llm_repair_showcase_matrix,
    main,
    render_llm_repair_showcase_matrix_markdown,
)


def test_llm_repair_showcase_matrix_classifies_required_case_types():
    suite = {
        "suite_name": "llm_showcase_suite",
        "suite_report_path": "out/github_repo_intelligence_suite.json",
        "runs": [
            {
                "name": "direct",
                "repo": "example/direct",
                "report_path": "out/direct/github_repo_intelligence.json",
                "status": "pass",
                "passed": True,
                "metrics": {
                    "repository_patch_generation_mode": "llm",
                    "repository_llm_patch_generation_status": "pass",
                    "repository_llm_patch_provider": "deepseek",
                    "repository_llm_patch_model": "deepseek-v4-pro",
                    "repository_llm_patch_api_key_present": True,
                    "repository_patch_generator_llm_candidate_count": 2,
                    "repository_test_patch_validation_status": "pass",
                    "repository_test_patch_validation_success_count": 1,
                    "repository_test_patch_validation_reflection_candidate_count": 0,
                    "repository_test_patch_validation_successful_reflection_count": 0,
                },
            },
            {
                "name": "reflection",
                "repo": "example/reflection",
                "report_path": "out/reflection/github_repo_intelligence.json",
                "status": "pass",
                "passed": True,
                "metrics": {
                    "repository_patch_generation_mode": "llm",
                    "repository_llm_patch_generation_status": "pass",
                    "repository_llm_patch_provider": "deepseek",
                    "repository_llm_patch_model": "deepseek-v4-pro",
                    "repository_llm_patch_api_key_present": True,
                    "repository_patch_generator_llm_candidate_count": 1,
                    "repository_test_patch_validation_status": "pass",
                    "repository_test_patch_validation_success_count": 1,
                    "repository_llm_reflection_status": "ready",
                    "repository_llm_reflection_provider": "deepseek",
                    "repository_llm_reflection_model": "deepseek-v4-pro",
                    "repository_test_patch_validation_reflection_candidate_count": 2,
                    "repository_test_patch_validation_successful_reflection_count": 1,
                },
            },
            {
                "name": "blocked",
                "repo": "example/blocked",
                "report_path": "out/blocked/llm_config_preflight.json",
                "status": "llm_config_blocked",
                "passed": False,
                "metrics": {
                    "status": "llm_config_blocked",
                    "blocker": "llm_config_missing_api_key",
                    "repository_patch_generation_mode": "llm",
                    "repository_llm_patch_generation_status": "blocked",
                    "repository_llm_patch_provider": "deepseek",
                    "repository_llm_patch_model": "deepseek-v4-pro",
                    "repository_llm_patch_api_key_present": False,
                    "repository_patch_generator_llm_candidate_count": 0,
                    "repository_test_patch_validation_success_count": 0,
                    "agent_answers_next_action": (
                        "Re-run the LLM repair smoke suite after environment setup."
                    ),
                },
            },
        ],
    }

    matrix = build_llm_repair_showcase_matrix([suite])

    assert matrix["status"] == "pass"
    assert matrix["class_counts"] == {
        "llm_blocker": 1,
        "llm_direct_success": 1,
        "llm_reflection_success": 1,
    }
    rows = {row["name"]: row for row in matrix["matrix"]}
    assert rows["direct"]["class"] == "llm_direct_success"
    assert rows["direct"]["repair_action_id"] == "generate_llm_patch_candidates"
    assert rows["reflection"]["class"] == "llm_reflection_success"
    assert rows["reflection"]["repair_action_id"] == "generate_llm_patch_candidates"
    assert rows["reflection"]["reflection_action_id"] == (
        "run_llm_patch_reflection_loop"
    )
    assert rows["blocked"]["class"] == "llm_blocker"
    assert rows["blocked"]["repair_action_id"] == "configure_llm_patch_api_key"
    assert "repair_action=configure_llm_patch_api_key" in (
        rows["blocked"]["agent_loop_evidence"]["plan"]
    )
    assert rows["blocked"]["agent_loop_evidence"]["replan"].startswith(
        "Re-run the LLM repair smoke suite"
    )

    markdown = render_llm_repair_showcase_matrix_markdown(matrix)

    assert "LLM Repair Showcase Matrix" in markdown
    assert "llm_direct_success" in markdown
    assert "llm_reflection_success" in markdown
    assert "llm_blocker" in markdown
    assert "generate_llm_patch_candidates" in markdown
    assert "run_llm_patch_reflection_loop" in markdown
    assert "- Verify:" in markdown


def test_llm_repair_showcase_matrix_cli_writes_blocker_artifacts_without_secret(
    tmp_path,
    capsys,
):
    suite_path = tmp_path / "github_repo_intelligence_suite.json"
    output_dir = tmp_path / "matrix"
    suite_path.write_text(
        json.dumps(
            {
                "suite_name": "llm_preflight_suite",
                "runs": [
                    {
                        "name": "missing_key",
                        "repo": "example/project",
                        "output_dir": "out/missing_key",
                        "report_path": "out/missing_key/llm_config_preflight.json",
                        "status": "llm_config_blocked",
                        "passed": False,
                        "metrics": {
                            "status": "llm_config_blocked",
                            "blocker": "llm_config_missing_api_key",
                            "repository_patch_generation_mode": "llm",
                            "repository_llm_patch_generation_status": "blocked",
                            "repository_llm_patch_provider": "deepseek",
                            "repository_llm_patch_model": "deepseek-v4-pro",
                            "repository_llm_patch_api_key_present": False,
                            "llm_config_next_actions": [
                                "Set DEEPSEEK_API_KEY in the current shell."
                            ],
                            "agent_answers_next_action": (
                                "Re-run the LLM repair smoke suite after the "
                                "environment variables are visible."
                            ),
                        },
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    main([str(suite_path), str(output_dir), "--format", "json"])
    stdout = capsys.readouterr().out
    json_path = output_dir / "llm_repair_showcase_matrix.json"
    markdown_path = output_dir / "llm_repair_showcase_matrix.md"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")

    assert payload["status"] == "incomplete"
    assert payload["class_counts"] == {"llm_blocker": 1}
    assert payload["matrix"][0]["class"] == "llm_blocker"
    assert "llm_config_missing_api_key" in markdown
    assert "sk-" not in json.dumps(payload)
    assert "sk-" not in stdout


def test_llm_repair_showcase_matrix_require_complete_exits_nonzero(tmp_path):
    suite_path = tmp_path / "github_repo_intelligence_suite.json"
    output_dir = tmp_path / "matrix"
    suite_path.write_text(
        json.dumps(
            {
                "suite_name": "incomplete_suite",
                "runs": [
                    {
                        "name": "blocked",
                        "repo": "example/project",
                        "status": "llm_config_blocked",
                        "metrics": {
                            "blocker": "llm_config_missing_api_key",
                            "repository_patch_generation_mode": "llm",
                            "repository_llm_patch_generation_status": "blocked",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        main([str(suite_path), str(output_dir), "--require-complete"])

    assert exc.value.code == 1
