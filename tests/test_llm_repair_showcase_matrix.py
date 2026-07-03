import json
from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.llm_repair_showcase_matrix import (
    build_llm_repair_evaluation_matrix,
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


def test_llm_repair_evaluation_matrix_reports_p6_metrics():
    runs = []
    for index in range(5):
        runs.append(
            _llm_success_run(
                name=f"direct_{index}",
                first_success_rank=1 if index < 3 else 2,
                reflection_successes=0,
                agreement_counts={"aligned": 1},
                token_count=100 + index,
            )
        )
    for index in range(3):
        runs.append(
            _llm_success_run(
                name=f"reflection_{index}",
                first_success_rank=3,
                reflection_successes=1,
                reflection_candidates=2,
                agreement_counts={
                    "judge_more_optimistic": 1,
                    "judge_more_conservative": 1,
                },
                token_count=200 + index,
            )
        )
    for index in range(12):
        runs.append(_llm_blocker_run(name=f"blocker_{index}"))
    suite = {
        "suite_name": "p6_llm_repair_suite",
        "runs": runs,
    }

    evaluation = build_llm_repair_evaluation_matrix([suite])
    metrics = evaluation["metrics_report"]

    assert evaluation["status"] == "pass"
    assert metrics["case_count"] == 20
    assert metrics["llm_direct_success_count"] == 5
    assert metrics["llm_reflection_success_count"] == 3
    assert metrics["llm_blocker_count"] == 12
    assert metrics["patch_success_at"] == {
        "1": 0.375,
        "3": 1.0,
        "5": 1.0,
    }
    assert metrics["rank_evidence_case_count"] == 8
    assert metrics["sandbox_pass_rate"] == 1.0
    assert metrics["judge_sandbox_agreement_counts"] == {
        "aligned": 5,
        "judge_more_conservative": 3,
        "judge_overoptimistic": 3,
    }
    assert metrics["judge_sandbox_agreement_rate"] == 0.4545
    assert metrics["agent_loop_trace_complete_count"] == 20
    assert metrics["target_summary"]["all_targets_met"] is True
    assert evaluation["matrix"][0]["patch_judge_authority"] == (
        "sandbox_pytest_decides_success"
    )


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
    evaluation_json_path = output_dir / "llm_repair_evaluation_matrix.json"
    evaluation_markdown_path = output_dir / "llm_repair_evaluation_matrix.md"
    metrics_json_path = output_dir / "llm_repair_metrics_report.json"
    metrics_markdown_path = output_dir / "llm_repair_metrics_report.md"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_json_path.read_text(encoding="utf-8"))
    metrics = json.loads(metrics_json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")

    assert payload["status"] == "incomplete"
    assert payload["class_counts"] == {"llm_blocker": 1}
    assert payload["matrix"][0]["class"] == "llm_blocker"
    assert evaluation["status"] == "incomplete"
    assert metrics["status"] == "incomplete"
    assert evaluation_markdown_path.exists()
    assert metrics_markdown_path.exists()
    assert metrics["sandbox_authority"] == "sandbox_pytest_decides_success"
    assert "llm_config_missing_api_key" in markdown
    assert "sk-" not in json.dumps(payload)
    assert "sk-" not in json.dumps(evaluation)
    assert "sk-" not in json.dumps(metrics)
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


def _llm_success_run(
    *,
    name: str,
    first_success_rank: int,
    reflection_successes: int,
    agreement_counts: dict[str, int],
    token_count: int,
    reflection_candidates: int = 0,
) -> dict:
    return {
        "name": name,
        "repo": f"example/{name}",
        "status": "pass",
        "passed": True,
        "runtime_seconds": 2.5,
        "metrics": {
            "repository_patch_generation_mode": "llm",
            "repository_llm_patch_generation_status": "pass",
            "repository_llm_patch_provider": "deepseek",
            "repository_llm_patch_model": "deepseek-v4-pro",
            "repository_llm_patch_api_key_present": True,
            "repository_patch_generator_llm_candidate_count": 3,
            "repository_test_patch_validation_status": "pass",
            "repository_test_patch_validation_candidate_count": 3,
            "repository_test_patch_validation_executed_count": 1,
            "repository_test_patch_validation_success_count": 1,
            "repository_test_patch_validation_first_success_rank": first_success_rank,
            "repository_test_patch_validation_reflection_mode": (
                "llm" if reflection_candidates else "none"
            ),
            "repository_test_patch_validation_reflection_candidate_count": (
                reflection_candidates
            ),
            "repository_test_patch_validation_successful_reflection_count": (
                reflection_successes
            ),
            "repository_test_patch_judge_mode": "llm",
            "repository_test_patch_judge_status": "ready",
            "repository_test_patch_judge_candidate_count": sum(
                agreement_counts.values()
            ),
            "repository_test_patch_judge_agreement_counts": agreement_counts,
            "repository_test_patch_judge_authority": (
                "sandbox_pytest_decides_success"
            ),
            "repository_llm_patch_total_tokens": token_count,
            "agent_answers_next_action": "Generate final agent report.",
        },
    }


def _llm_blocker_run(*, name: str) -> dict:
    return {
        "name": name,
        "repo": f"example/{name}",
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
            "agent_answers_next_action": "Configure the LLM key and rerun.",
        },
    }
