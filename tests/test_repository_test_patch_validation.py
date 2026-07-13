import json
from pathlib import Path

from code_intelligence_agent.agents.llm_client import StaticLLMClient
from code_intelligence_agent.core.models import PatchCandidate
from code_intelligence_agent.evaluation.repository_test_fault_localization import (
    build_repository_test_fault_localization,
)
from code_intelligence_agent.evaluation.repository_test_patch_candidates import (
    build_repository_test_patch_candidates,
)
from code_intelligence_agent.evaluation.repository_test_patch_validation import (
    build_repository_test_patch_validation,
    render_repository_test_patch_validation_markdown,
    write_repository_test_patch_validation_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_reflection_trace import (
    build_repository_test_reflection_trace,
    render_repository_test_reflection_trace_markdown,
)
from code_intelligence_agent.search.patch_judge import PatchJudgment
from code_intelligence_agent.tools.diff_utils import render_unified_diff


def test_repository_test_reflection_trace_recommends_failure_specific_strategies():
    trace = build_repository_test_reflection_trace(
        {
            "status": "fail",
            "reason": "no_candidate_passed_repository_tests",
            "reflection_enabled": False,
            "results": [
                {
                    "candidate_id": "syntax_candidate",
                    "depth": 0,
                    "success": False,
                    "rule_id": "manual",
                    "variant": "bad_syntax",
                    "failure_type": "syntax_error",
                    "failure_reason": "SyntaxError: invalid syntax",
                },
                {
                    "candidate_id": "apply_candidate",
                    "depth": 0,
                    "success": False,
                    "rule_id": "manual",
                    "variant": "stale_diff",
                    "failure_type": "patch_apply_error",
                    "failure_reason": "original source block not found",
                },
                {
                    "candidate_id": "logic_candidate",
                    "depth": 0,
                    "success": False,
                    "rule_id": "manual",
                    "variant": "wrong_logic",
                    "failure_type": "test_failure",
                    "failure_reason": "AssertionError",
                },
            ],
        }
    )
    markdown = render_repository_test_reflection_trace_markdown(trace)

    assert trace["reason"] == "depth0_failures_without_reflection"
    assert trace["initial_failure_type_counts"] == {
        "patch_apply_error": 1,
        "syntax_error": 1,
        "test_failure": 1,
    }
    assert trace["initial_strategy_counts"] == {
        "refine_logic_against_failing_assertion": 1,
        "regenerate_ast_valid_patch": 1,
        "regenerate_minimal_applicable_diff": 1,
    }
    strategy_ids = {
        item["id"] for item in trace["recommended_reflection_strategies"]
    }
    assert "regenerate_ast_valid_patch" in strategy_ids
    assert "regenerate_minimal_applicable_diff" in strategy_ids
    assert "refine_logic_against_failing_assertion" in strategy_ids
    assert trace["initial_failures"][0]["reflection_strategy_id"] == (
        "regenerate_ast_valid_patch"
    )
    assert any(
        "Regenerate an AST-valid patch" in action
        for action in trace["next_actions"]
    )
    assert "## Recommended Reflection Strategies" in markdown
    assert "regenerate_minimal_applicable_diff" in markdown


def test_repository_test_patch_validation_executes_candidates(tmp_path):
    _write_validation_repo(tmp_path)
    localization = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=tmp_path,
        top_k=3,
    )
    patch_candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=tmp_path,
        candidate_limit=5,
    )

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=2,
        timeout=10,
        regression_pytest_args=["tests"],
        regression_validation_command="python -m pytest -q tests",
    )
    paths = write_repository_test_patch_validation_artifacts(
        payload,
        tmp_path / "out",
    )
    markdown = render_repository_test_patch_validation_markdown(payload)

    assert patch_candidates["status"] == "pass"
    assert patch_candidates["candidate_count"] == 2
    assert payload["status"] == "pass"
    assert payload["reason"] == "patch_validation_success"
    assert payload["candidate_count"] == 2
    assert payload["executed_count"] == 2
    assert payload["depth0_executed_count"] == 2
    assert payload["success_count"] == 1
    assert payload["verified_repair"] is True
    assert payload["verification_claim"] == "verified_repair"
    assert payload["layered_validation"]["layers"]["import_validation"][
        "status"
    ] == "pass"
    assert payload["repair_ready"] is True
    assert payload["repair_validation_scope"] == "narrow_and_regression"
    assert payload["regression_ready"] is True
    assert payload["regression_validation"]["status"] == "pass"
    assert payload["regression_validation"]["pytest_args"] == ["tests"]
    assert payload["regression_validation"]["passed"] == 1
    assert payload["reflection_candidate_count"] == 0
    assert payload["successful_reflection_candidate_count"] == 0
    assert payload["best_candidate_success"] is True
    assert payload["best_candidate_rule_id"] == "possible_index_overrun"
    assert payload["best_patch"]["candidate_id"] == payload["best_candidate_id"]
    assert payload["best_patch"]["relative_file_path"] == "sample.py"
    assert "range(len(values) - 1)" in payload["best_patch"]["new_source"]
    assert payload["failure_type_counts"]["success"] == 1
    assert payload["failure_type_counts"]["test_failure"] == 1
    assert payload["successful_candidates"][0]["rule_id"] == "possible_index_overrun"
    assert "Repository Test Patch Validation" in markdown
    assert "patch_validation_success" in markdown
    assert "Regression Validation" in markdown
    assert Path(paths["repository_test_patch_validation_json"]).exists()
    assert Path(paths["repository_test_patch_validation_markdown"]).exists()
    assert Path(paths["repository_test_reflection_trace_json"]).exists()
    assert Path(paths["repository_test_reflection_trace_markdown"]).exists()
    assert Path(paths["reflection_trace_json"]).exists()
    assert Path(paths["reflection_trace_markdown"]).exists()
    reflection_trace = json.loads(
        Path(paths["reflection_trace_json"]).read_text(encoding="utf-8")
    )
    assert reflection_trace["status"] == "pass"
    assert reflection_trace["reason"] == "depth0_success_no_reflection_needed"
    assert reflection_trace["final_outcome"]["repair_ready"] is True
    assert reflection_trace["initial_failure_type_counts"] == {"test_failure": 1}
    assert reflection_trace["reflection_failure_type_counts"] == {}
    assert reflection_trace["reflection_parent_failure_type_counts"] == {}
    assert reflection_trace["successful_reflection_parent_failure_type_counts"] == {}
    assert reflection_trace["initial_failures"][0]["failure_type"] == (
        "test_failure"
    )
    assert Path(paths["repository_test_repair_patch"]).exists()
    assert "range(len(values) - 1)" in Path(
        paths["repository_test_repair_patch"]
    ).read_text(encoding="utf-8")


def test_repository_test_patch_validation_refines_failed_candidate(tmp_path):
    _write_validation_repo(tmp_path)
    localization = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=tmp_path,
        top_k=3,
    )
    patch_candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=tmp_path,
        candidate_limit=5,
    )
    conservative = [
        candidate
        for candidate in patch_candidates["candidates"]
        if candidate["metadata"]["variant"] == "overly_conservative_range_bound"
    ][0]
    patch_candidates = {
        **patch_candidates,
        "candidate_count": 1,
        "candidates": [conservative],
    }

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=1,
        reflection_rounds=1,
        timeout=10,
    )
    paths = write_repository_test_patch_validation_artifacts(
        payload,
        tmp_path / "out",
    )
    markdown = render_repository_test_patch_validation_markdown(payload)
    reflection_trace = json.loads(
        Path(paths["reflection_trace_json"]).read_text(encoding="utf-8")
    )

    assert payload["status"] == "pass"
    assert payload["reason"] == "patch_validation_reflection_success"
    assert payload["candidate_count"] == 1
    assert payload["executed_count"] == 2
    assert payload["depth0_executed_count"] == 1
    assert payload["success_count"] == 1
    assert payload["candidate_patch"] is True
    assert payload["targeted_candidate_ready"] is True
    assert payload["verified_repair"] is False
    assert payload["verification_claim"] == "targeted_candidate_unverified"
    assert payload["repair_ready"] is False
    assert payload["reflection_enabled"] is True
    assert payload["reflection_candidate_count"] == 1
    assert payload["successful_reflection_candidate_count"] == 1
    assert payload["max_depth_executed"] == 1
    assert payload["best_candidate_success"] is True
    assert payload["best_candidate_variant"] == "reflection_shrink_range_upper_bound"
    assert payload["best_patch"]["depth"] == 1
    assert payload["best_patch"]["parent_candidate_id"] == conservative["id"]
    assert payload["best_patch"]["variant"] == "reflection_shrink_range_upper_bound"
    assert payload["failure_type_counts"] == {"success": 1, "test_failure": 1}
    assert payload["successful_candidates"][0]["depth"] == 1
    assert payload["successful_candidates"][0]["parent_candidate_id"] == (
        conservative["id"]
    )
    assert reflection_trace["status"] == "pass"
    assert reflection_trace["reason"] == "reflection_repaired_candidate"
    assert reflection_trace["reflection_candidate_count"] == 1
    assert reflection_trace["successful_reflection_candidate_count"] == 1
    assert reflection_trace["initial_failures"][0]["candidate_id"] == (
        conservative["id"]
    )
    assert reflection_trace["reflection_steps"][0]["parent_candidate_id"] == (
        conservative["id"]
    )
    assert reflection_trace["reflection_steps"][0]["success"] is True
    assert reflection_trace["reflection_steps"][0]["reflection_strategy_id"] == (
        "refine_logic_against_failing_assertion"
    )
    assert reflection_trace["reflection_steps"][0]["reflection_evidence_complete"] is True
    assert reflection_trace["reflection_steps"][0]["reflection_evidence_missing"] == []
    assert reflection_trace["reflection_steps"][0]["parent_patch_audit"][
        "diff_fingerprint"
    ]
    assert reflection_trace["reflection_steps"][0]["refined_child_patch_audit"][
        "diff_fingerprint"
    ]
    assert reflection_trace["reflection_steps"][0]["refined_child_safety_gate"][
        "status"
    ] == "pass"
    assert reflection_trace["reflection_steps"][0]["refined_child_sandbox_result"][
        "status"
    ] == "pass"
    assert reflection_trace["reflection_evidence_complete_count"] == 1
    assert reflection_trace["reflection_evidence_incomplete_count"] == 0
    assert reflection_trace["initial_failure_type_counts"] == {"test_failure": 1}
    assert reflection_trace["reflection_failure_type_counts"] == {"success": 1}
    assert reflection_trace["reflection_parent_failure_type_counts"] == {
        "test_failure": 1
    }
    assert reflection_trace["successful_reflection_parent_failure_type_counts"] == {
        "test_failure": 1
    }
    assert reflection_trace["final_outcome"]["best_patch_depth"] == 1
    assert any(
        result["depth"] == 1
        and result["parent_candidate_id"] == conservative["id"]
        and result["success"] is True
        for result in payload["results"]
    )
    assert "patch_validation_reflection_success" in markdown
    assert "reflection_shrink_range_upper_bound" in markdown
    trace_markdown = Path(paths["reflection_trace_markdown"]).read_text(
        encoding="utf-8"
    )
    assert "## Reflection Failure Taxonomy" in trace_markdown
    assert "## Reflection Evidence Audit" in trace_markdown
    assert "Initial Failure Types: test_failure=1" in trace_markdown
    assert "Successful Reflection Parent Failure Types: test_failure=1" in (
        trace_markdown
    )


def test_repository_test_patch_validation_llm_mode_missing_key_keeps_depth0(
    tmp_path,
    monkeypatch,
):
    for env_name in [
        "CIA_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "CIA_LLM_PROVIDER",
        "CIA_LLM_MODEL",
        "CIA_LLM_BASE_URL",
    ]:
        monkeypatch.delenv(env_name, raising=False)
    _write_validation_repo(tmp_path)
    patch_candidates, _ = _conservative_patch_candidates(tmp_path)

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=1,
        reflection_mode="llm",
        reflection_rounds=1,
        timeout=10,
    )

    assert payload["status"] == "fail"
    assert payload["reason"] == "no_candidate_passed_repository_tests"
    assert payload["executed_count"] == 1
    assert payload["reflection_enabled"] is False
    assert payload["reflection_mode"] == "llm"
    assert payload["reflection_refiner_status"] == "unavailable"
    assert payload["reflection_refiner_reason"] == "missing_api_key:CIA_LLM_API_KEY"
    audit = payload["llm_reflection_config_audit"]
    assert audit["provider"] == "deepseek"
    assert audit["model"] == "deepseek-v4-pro"
    assert audit["api_key_present"] is False
    trace = build_repository_test_reflection_trace(payload)
    trace_markdown = render_repository_test_reflection_trace_markdown(trace)
    assert trace["llm_reflection_config_audit"]["provider"] == "deepseek"
    assert trace["llm_reflection_config_audit"]["model"] == "deepseek-v4-pro"
    assert trace["llm_reflection_config_audit"]["api_key_present"] is False
    assert "LLM Reflection Config: provider=`deepseek`" in trace_markdown


def test_repository_test_patch_validation_reflection_safety_gate_blocks_child(
    tmp_path,
):
    _write_validation_repo(tmp_path)
    patch_candidates, conservative = _conservative_patch_candidates(tmp_path)

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=1,
        reflection_rounds=1,
        refiner=_UnsafeReflectionRefiner(),
        timeout=10,
    )
    trace = build_repository_test_reflection_trace(payload)
    child = payload["results"][1]
    step = trace["reflection_steps"][0]

    assert payload["status"] == "fail"
    assert payload["reason"] == "no_candidate_passed_repository_tests"
    assert payload["reflection_candidate_count"] == 1
    assert payload["successful_reflection_candidate_count"] == 0
    assert child["parent_candidate_id"] == conservative["id"]
    assert child["failure_type"] == "safety_gate_blocked"
    assert child["command"] == ["safety_gate"]
    assert child["safety_gate"]["status"] == "blocked"
    assert "invalid_python_ast" in child["safety_gate"]["reasons"]
    assert step["parent_candidate_id"] == conservative["id"]
    assert step["parent_failure_type"] == "test_failure"
    assert step["reflection_strategy_id"] == (
        "refine_logic_against_failing_assertion"
    )
    assert step["failure_type"] == "safety_gate_blocked"
    assert step["refined_child_safety_gate"]["status"] == "blocked"
    assert step["refined_child_sandbox_result"]["status"] == (
        "blocked_before_pytest"
    )
    assert step["patch_apply_status"] == "not_applied_safety_gate_blocked"
    assert step["reflection_evidence_complete"] is True
    assert trace["reflection_evidence_complete_count"] == 1
    assert trace["reflection_evidence_incomplete_count"] == 0


def test_repository_test_patch_validation_llm_refiner_repairs_failed_candidate(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CIA_LLM_API_KEY", "fake-key")
    monkeypatch.setenv("CIA_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("CIA_LLM_MODEL", "deepseekv4PRO")
    _write_validation_repo(tmp_path)
    patch_candidates, conservative = _conservative_patch_candidates(tmp_path)
    fixed_source = (
        "def shift_left(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values) - 1):\n"
        "        shifted.append(values[i + 1])\n"
        "    return shifted\n"
    )
    client = StaticLLMClient(json.dumps({"fixed_source": fixed_source}))
    monkeypatch.setattr(
        "code_intelligence_agent.evaluation.repository_test_patch_validation.create_patch_client",
        lambda: client,
    )

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=1,
        reflection_mode="llm",
        reflection_rounds=1,
        timeout=10,
    )

    assert payload["status"] == "pass"
    assert payload["reason"] == "patch_validation_reflection_success"
    assert payload["reflection_enabled"] is True
    assert payload["reflection_mode"] == "llm"
    assert payload["reflection_refiner_status"] == "ready"
    assert payload["reflection_candidate_count"] == 1
    assert payload["successful_reflection_candidate_count"] == 1
    assert payload["llm_reflection_attempt_count"] == 1
    assert payload["llm_reflection_audit"][0]["parent_patch_id"] == conservative["id"]
    assert payload["llm_reflection_audit"][0]["response_parse"]["status"] == (
        "pass"
    )
    assert payload["llm_reflection_audit"][0]["accepted_candidate_count"] == 1
    assert payload["best_candidate_rule_id"] == "llm_reflection_patch"
    assert payload["successful_candidates"][0]["parent_candidate_id"] == (
        conservative["id"]
    )
    prompt = json.loads(client.prompts[0])
    assert prompt["parent_candidate"]["id"] == conservative["id"]
    assert prompt["reflection_strategy"]["id"] == "semantic_repair"
    assert prompt["failure_evidence"]["failed_patch_fingerprint"]
    assert prompt["execution_feedback"]["failure_type"] == "test_failure"
    assert prompt["function"]["previous_fixed_source"] == conservative["new_source"]
    markdown = render_repository_test_patch_validation_markdown(payload)
    assert "## LLM Reflection Audit" in markdown
    trace = build_repository_test_reflection_trace(payload)
    assert trace["llm_reflection_attempt_count"] == 1


def test_repository_test_patch_validation_llm_patch_judge_missing_key_is_audit_only(
    tmp_path,
    monkeypatch,
):
    for env_name in [
        "CIA_JUDGE_API_KEY",
        "DEEPSEEK_API_KEY",
        "CIA_JUDGE_PROVIDER",
        "CIA_JUDGE_MODEL",
        "CIA_JUDGE_BASE_URL",
    ]:
        monkeypatch.delenv(env_name, raising=False)
    _write_validation_repo(tmp_path)
    localization = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=tmp_path,
        top_k=3,
    )
    patch_candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=tmp_path,
        candidate_limit=5,
    )

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=1,
        reflection_rounds=0,
        patch_judge_mode="llm",
        timeout=10,
    )
    markdown = render_repository_test_patch_validation_markdown(payload)

    assert payload["patch_judge_enabled"] is False
    assert payload["patch_judge_mode"] == "llm"
    assert payload["patch_judge_status"] == "unavailable"
    assert payload["patch_judge_reason"] == "missing_api_key:CIA_JUDGE_API_KEY"
    assert payload["patch_judge_candidate_count"] == 0
    assert payload["patch_judge_authority"] == "sandbox_pytest_decides_success"
    assert payload["patch_judge_config_audit"]["provider"] == "deepseek"
    assert payload["patch_judge_config_audit"]["model"] == "deepseek-v4-pro"
    assert payload["patch_judge_config_audit"]["api_key_present"] is False
    assert "Patch Judge" in markdown
    assert "sandbox_pytest_decides_success" in markdown


def test_repository_test_patch_validation_patch_judge_records_outcome_counts(
    tmp_path,
):
    _write_validation_repo(tmp_path)
    localization = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=tmp_path,
        top_k=3,
    )
    patch_candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=tmp_path,
        candidate_limit=5,
    )

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=2,
        reflection_rounds=0,
        patch_judge=_AlwaysPreferPatchJudge(),
        timeout=10,
    )
    markdown = render_repository_test_patch_validation_markdown(payload)

    assert payload["patch_judge_candidate_count"] == 2
    assert payload["patch_judge_outcome_counts"]["accept_success"] == 1
    assert payload["patch_judge_outcome_counts"]["accept_failure"] == 1
    assert payload["patch_judge_outcome_counts"]["judged_sandbox_success"] == 1
    assert payload["patch_judge_outcome_counts"]["judged_sandbox_failure"] == 1
    assert payload["patch_judge_outcome_counts"]["outcome_mismatch"] == 1
    assert "Outcome Counts" in markdown


def test_repository_test_patch_validation_patch_judge_cannot_override_sandbox_fail(
    tmp_path,
):
    _write_validation_repo(tmp_path)
    patch_candidates, _ = _conservative_patch_candidates(tmp_path)
    judge = _AlwaysPreferPatchJudge()

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=1,
        reflection_rounds=0,
        patch_judge=judge,
        patch_judge_weight=1.0,
        timeout=10,
    )

    assert payload["status"] == "fail"
    assert payload["reason"] == "no_candidate_passed_repository_tests"
    assert payload["success_count"] == 0
    assert payload["repair_ready"] is False
    assert payload["patch_judge_enabled"] is True
    assert payload["patch_judge_mode"] == "custom"
    assert payload["patch_judge_status"] == "ready"
    assert payload["patch_judge_candidate_count"] == 1
    assert payload["patch_judge_verdict_counts"] == {"prefer": 1}
    assert payload["patch_judge_outcome_counts"]["accept_failure"] == 1
    assert payload["patch_judge_outcome_counts"]["outcome_mismatch"] == 1
    assert payload["patch_judge_authority"] == "sandbox_pytest_decides_success"
    assert payload["results"][0]["patch_judgment"]["verdict"] == "prefer"
    assert payload["results"][0]["success"] is False


def test_repository_test_patch_validation_skips_without_pytest_args(tmp_path):
    _write_validation_repo(tmp_path)
    payload = build_repository_test_patch_validation(
        {
            "status": "pass",
            "candidate_count": 1,
            "recommended_pytest_args": [],
            "candidates": [
                {
                    "id": "candidate",
                    "relative_file_path": "sample.py",
                    "old_source": "def f():\n    return 1\n",
                    "new_source": "def f():\n    return 2\n",
                }
            ],
        },
        repository_root=tmp_path,
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "validation_args_missing"
    assert payload["executed_count"] == 0
    assert payload["repair_ready"] is False
    assert payload["best_patch"] == {}
    assert payload["next_actions"]
    paths = write_repository_test_patch_validation_artifacts(
        payload,
        tmp_path / "out",
    )
    reflection_trace = json.loads(
        Path(paths["reflection_trace_json"]).read_text(encoding="utf-8")
    )
    assert reflection_trace["reason"] == "patch_validation_not_executed"
    assert reflection_trace["initial_failure_type_counts"] == {}
    assert reflection_trace["reflection_failure_type_counts"] == {}
    assert reflection_trace["reflection_parent_failure_type_counts"] == {}
    assert reflection_trace["successful_reflection_parent_failure_type_counts"] == {}
    trace_markdown = Path(paths["reflection_trace_markdown"]).read_text(
        encoding="utf-8"
    )
    assert "## Reflection Failure Taxonomy" in trace_markdown
    assert "Initial Failure Types: none" in trace_markdown


def test_repository_test_patch_validation_blocks_safety_gated_candidates(
    tmp_path,
):
    sample_path = tmp_path / "sample.py"
    old_source = "def pick(values):\n    return values[1]\n"
    new_source = "def pick(values):\n    return values[0]\n"
    sample_path.write_text(old_source, encoding="utf-8")
    patch_candidates = {
        "status": "pass",
        "candidate_count": 1,
        "recommended_validation_command": (
            "python -m pytest -q tests/test_sample.py::test_pick"
        ),
        "recommended_pytest_args": ["tests/test_sample.py::test_pick"],
        "safety_gate": {
            "status": "blocked",
            "blocked_count": 1,
            "passed_count": 0,
            "required_checks": [
                "ast_valid",
                "scope_limited",
                "minimal_diff",
            ],
        },
        "candidates": [
            {
                "id": "blocked_patch",
                "relative_file_path": "sample.py",
                "target_function_id": "sample.py::pick",
                "target_function_name": "pick",
                "rule_id": "unsafe_rule",
                "description": "Unsafe oversized patch.",
                "old_source": old_source,
                "new_source": new_source,
                "diff": render_unified_diff(old_source, new_source, "sample.py"),
                "metadata": {
                    "variant": "unsafe",
                    "safety_gate": {
                        "status": "blocked",
                        "ast_valid": False,
                        "scope_limited": False,
                        "minimal_diff": False,
                        "reasons": ["patch_too_large"],
                    },
                },
            }
        ],
    }

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=1,
        reflection_rounds=0,
        timeout=10,
    )
    markdown = render_repository_test_patch_validation_markdown(payload)

    assert payload["status"] == "skipped"
    assert payload["reason"] == "all_candidates_blocked_by_safety_gate"
    assert payload["input_candidate_count"] == 1
    assert payload["candidate_count"] == 0
    assert payload["executed_count"] == 0
    assert payload["repair_ready"] is False
    assert payload["best_patch"] == {}
    assert payload["safety_blocked_candidate_count"] == 1
    assert payload["safety_blocked_candidates"][0]["candidate_id"] == (
        "blocked_patch"
    )
    assert payload["safety_blocked_candidates"][0]["reasons"] == [
        "patch_too_large"
    ]
    assert "patch_too_large" in markdown


def test_repository_test_patch_validation_preserves_parameterized_nodeid(tmp_path):
    nodeid = "tests/test_sample.py::test_shift_left[pkg::empty value]"
    old_source, new_source = _write_parameterized_validation_repo(tmp_path)
    patch_candidates = {
        "status": "pass",
        "candidate_count": 1,
        "recommended_validation_command": (
            f"python -m pytest -q --maxfail=1 '{nodeid}'"
        ),
        "recommended_pytest_args": ["--maxfail=1", nodeid],
        "targets": [{"function_id": "sample.py::shift_left", "score": 0.9}],
        "candidates": [
            {
                "id": "candidate",
                "relative_file_path": "sample.py",
                "target_function_id": "sample.py::shift_left",
                "target_function_name": "shift_left",
                "rule_id": "possible_index_overrun",
                "description": "Shrink range upper bound.",
                "old_source": old_source,
                "new_source": new_source,
                "metadata": {"variant": "range_upper_bound"},
            }
        ],
    }

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=1,
        reflection_rounds=0,
        timeout=10,
    )

    assert payload["status"] == "pass"
    assert payload["reason"] == "patch_validation_success"
    assert payload["recommended_pytest_args"] == ["--maxfail=1", nodeid]
    assert payload["candidate_patch"] is True
    assert payload["verified_repair"] is False
    assert payload["verification_claim"] == "targeted_candidate_unverified"
    assert payload["repair_ready"] is False
    assert payload["best_patch"]["relative_file_path"] == "sample.py"
    assert payload["results"][0]["success"] is True
    assert payload["results"][0]["passed"] == 1
    assert nodeid in payload["results"][0]["command"]


def test_repository_test_patch_validation_blocks_ready_on_regression_failure(
    tmp_path,
):
    old_source, new_source = _write_regression_validation_repo(tmp_path)
    patch_candidates = {
        "status": "pass",
        "candidate_count": 1,
        "recommended_validation_command": (
            "python -m pytest -q tests/test_sample.py::test_large_value"
        ),
        "recommended_pytest_args": ["tests/test_sample.py::test_large_value"],
        "targets": [{"function_id": "sample.py::classify", "score": 0.9}],
        "candidates": [
            {
                "id": "candidate",
                "relative_file_path": "sample.py",
                "target_function_id": "sample.py::classify",
                "target_function_name": "classify",
                "rule_id": "manual_regression_probe",
                "description": "Fix large values.",
                "old_source": old_source,
                "new_source": new_source,
                "diff": render_unified_diff(old_source, new_source, "sample.py"),
                "metadata": {"variant": "large_value_only"},
            }
        ],
    }

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=1,
        reflection_rounds=0,
        timeout=10,
        regression_pytest_args=["tests"],
        regression_validation_command="python -m pytest -q tests",
    )
    paths = write_repository_test_patch_validation_artifacts(
        payload,
        tmp_path / "out",
    )

    assert payload["status"] == "pass"
    assert payload["best_candidate_success"] is True
    assert payload["repair_ready"] is False
    assert payload["repair_validation_scope"] == "regression_failed"
    assert payload["regression_ready"] is False
    assert payload["regression_validation"]["status"] == "fail"
    assert payload["regression_validation"]["reason"] == "regression_tests_failed"
    assert payload["regression_validation"]["baseline_status"] == "fail"
    assert payload["regression_validation"]["baseline_failed_unchanged"] is False
    assert payload["regression_validation"]["failed"] == 1
    assert "repository_test_repair_patch" not in paths


def test_repository_test_patch_validation_allows_unchanged_baseline_failure(
    tmp_path,
):
    old_source, new_source = _write_unchanged_baseline_failure_repo(tmp_path)
    patch_candidates = {
        "status": "pass",
        "candidate_count": 1,
        "recommended_validation_command": (
            "python -m pytest -q tests/test_sample.py::test_large_value"
        ),
        "recommended_pytest_args": ["tests/test_sample.py::test_large_value"],
        "targets": [{"function_id": "sample.py::classify", "score": 0.9}],
        "candidates": [
            {
                "id": "candidate",
                "relative_file_path": "sample.py",
                "target_function_id": "sample.py::classify",
                "target_function_name": "classify",
                "rule_id": "manual_regression_probe",
                "description": "Fix large values.",
                "old_source": old_source,
                "new_source": new_source,
                "diff": render_unified_diff(old_source, new_source, "sample.py"),
                "metadata": {"variant": "large_value_only"},
            }
        ],
    }

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=1,
        reflection_rounds=1,
        timeout=10,
        regression_pytest_args=["tests/test_unrelated.py"],
        regression_validation_command="python -m pytest -q tests/test_unrelated.py",
    )
    paths = write_repository_test_patch_validation_artifacts(
        payload,
        tmp_path / "out",
    )

    assert payload["status"] == "pass"
    assert payload["best_candidate_success"] is True
    assert payload["candidate_patch"] is True
    assert payload["verified_repair"] is False
    assert payload["verification_claim"] == (
        "targeted_candidate_with_baseline_caveat"
    )
    assert payload["repair_ready"] is False
    assert payload["repair_validation_scope"] == (
        "narrow_and_unchanged_regression_baseline"
    )
    assert payload["regression_ready"] is False
    assert payload["regression_validation"]["status"] == (
        "baseline_failed_unchanged"
    )
    assert payload["regression_validation"]["reason"] == (
        "regression_baseline_failed_unchanged"
    )
    assert payload["regression_validation"]["baseline_status"] == "fail"
    assert payload["regression_validation"]["baseline_failure_type"] == (
        "syntax_error"
    )
    assert payload["regression_validation"]["baseline_failure_signature"] == (
        payload["regression_validation"]["patched_failure_signature"]
    )
    assert "tests/test_unrelated.py" in (
        payload["regression_validation"]["baseline_failure_signature"]
    )
    assert "repository_test_failure_overlay_checkout" not in (
        payload["regression_validation"]["baseline_failure_signature"]
    )
    assert "cia_sandbox" not in (
        payload["regression_validation"]["patched_failure_signature"]
    )
    assert payload["regression_validation"]["baseline_failed_unchanged"] is True
    assert payload["regression_reflection_candidate_count"] == 0
    assert payload["successful_regression_reflection_candidate_count"] == 0
    assert "repository_test_repair_patch" not in paths
    assert "repository_test_candidate_patch" in paths


def test_repository_test_patch_validation_reflects_regression_failure(tmp_path):
    old_source, new_source = _write_regression_validation_repo(tmp_path)
    patch_candidates = {
        "status": "pass",
        "candidate_count": 1,
        "recommended_validation_command": (
            "python -m pytest -q tests/test_sample.py::test_large_value"
        ),
        "recommended_pytest_args": ["tests/test_sample.py::test_large_value"],
        "targets": [{"function_id": "sample.py::classify", "score": 0.9}],
        "candidates": [
            {
                "id": "candidate",
                "relative_file_path": "sample.py",
                "target_function_id": "sample.py::classify",
                "target_function_name": "classify",
                "rule_id": "manual_regression_probe",
                "description": "Fix large values.",
                "old_source": old_source,
                "new_source": new_source,
                "metadata": {"variant": "large_value_only"},
            }
        ],
    }

    payload = build_repository_test_patch_validation(
        patch_candidates,
        repository_root=tmp_path,
        validation_limit=1,
        reflection_rounds=1,
        reflection_width=1,
        refiner=_RegressionRefiner(),
        timeout=10,
        regression_pytest_args=["tests"],
        regression_validation_command="python -m pytest -q tests",
    )
    paths = write_repository_test_patch_validation_artifacts(
        payload,
        tmp_path / "out",
    )
    reflection_trace = json.loads(
        Path(paths["reflection_trace_json"]).read_text(encoding="utf-8")
    )

    assert payload["status"] == "pass"
    assert payload["reason"] == "patch_validation_reflection_success"
    assert payload["best_candidate_success"] is True
    assert payload["best_candidate_variant"] == (
        "regression_reflection_preserve_small"
    )
    assert payload["best_patch"]["depth"] == 1
    assert payload["best_patch"]["parent_candidate_id"] == "candidate"
    assert payload["repair_ready"] is True
    assert payload["repair_validation_scope"] == "narrow_and_regression"
    assert payload["regression_validation"]["status"] == "pass"
    assert payload["reflection_candidate_count"] == 1
    assert payload["successful_reflection_candidate_count"] == 1
    assert payload["regression_reflection_candidate_count"] == 1
    assert payload["successful_regression_reflection_candidate_count"] == 1
    assert payload["results"][1]["regression_reflection"] is True
    assert payload["results"][1]["regression_reflection_parent_id"] == "candidate"
    assert "repository_test_repair_patch" in paths
    assert reflection_trace["reason"] == "reflection_repaired_candidate"
    assert reflection_trace["final_outcome"]["repair_ready"] is True
    assert reflection_trace["reflection_steps"][0]["parent_candidate_id"] == (
        "candidate"
    )


class _RegressionRefiner:
    def refine_many(
        self,
        *,
        repo_path,
        previous_patch,
        execution_result,
        round_index,
        limit,
    ):
        del repo_path
        assert execution_result.success is False
        assert execution_result.failed == 1
        fixed_source = (
            "def classify(value):\n"
            "    if value == 0:\n"
            "        return 'zero'\n"
            "    if value >= 10:\n"
            "        return 'large'\n"
            "    return 'small'\n"
        )
        return [
            PatchCandidate(
                id=f"{previous_patch.id}::regression_reflection",
                target_file=previous_patch.target_file,
                relative_file_path=previous_patch.relative_file_path,
                target_function_id=previous_patch.target_function_id,
                target_function_name=previous_patch.target_function_name,
                rule_id=previous_patch.rule_id,
                description="Preserve small-value behavior while fixing large values.",
                old_source=previous_patch.old_source,
                new_source=fixed_source,
                diff=render_unified_diff(
                    previous_patch.old_source,
                    fixed_source,
                    previous_patch.relative_file_path,
                ),
                metadata={
                    **previous_patch.metadata,
                    "variant": "regression_reflection_preserve_small",
                    "reflection_round_index": round_index,
                    "reflection_width_limit": limit,
                },
            )
        ]


class _UnsafeReflectionRefiner:
    def refine_many(
        self,
        *,
        repo_path,
        previous_patch,
        execution_result,
        round_index,
        limit,
    ):
        del repo_path, execution_result, limit
        invalid_source = "def shift_left(values):\n    if\n"
        return [
            PatchCandidate(
                id=f"{previous_patch.id}::unsafe_reflection",
                target_file=previous_patch.target_file,
                relative_file_path=previous_patch.relative_file_path,
                target_function_id=previous_patch.target_function_id,
                target_function_name=previous_patch.target_function_name,
                rule_id=previous_patch.rule_id,
                description="Invalid reflection patch for safety gate testing.",
                old_source=previous_patch.old_source,
                new_source=invalid_source,
                diff=render_unified_diff(
                    previous_patch.old_source,
                    invalid_source,
                    previous_patch.relative_file_path,
                ),
                metadata={
                    **previous_patch.metadata,
                    "variant": "unsafe_reflection_invalid_ast",
                    "reflection_round_index": round_index,
                },
            )
        ]


class _AlwaysPreferPatchJudge:
    def judge_patch(self, **kwargs):
        return PatchJudgment(
            score=1.0,
            verdict="prefer",
            reason="Looks plausible, but sandbox remains authoritative.",
            model="static-test-judge",
        )


def _conservative_patch_candidates(root):
    localization = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=root,
        top_k=3,
    )
    patch_candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=root,
        candidate_limit=5,
    )
    conservative = [
        candidate
        for candidate in patch_candidates["candidates"]
        if candidate["metadata"]["variant"] == "overly_conservative_range_bound"
    ][0]
    return {
        **patch_candidates,
        "candidate_count": 1,
        "candidates": [conservative],
    }, conservative


def _write_validation_repo(root):
    (root / "sample.py").write_text(
        "def shift_left(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values)):\n"
        "        shifted.append(values[i + 1])\n"
        "    return shifted\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "from sample import shift_left\n\n"
        "def test_shift_left_short_and_many():\n"
        "    assert shift_left([1]) == []\n"
        "    assert shift_left([1, 2, 3]) == [2, 3]\n",
        encoding="utf-8",
    )


def _write_parameterized_validation_repo(root):
    old_source = (
        "def shift_left(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values)):\n"
        "        shifted.append(values[i + 1])\n"
        "    return shifted\n"
    )
    new_source = (
        "def shift_left(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values) - 1):\n"
        "        shifted.append(values[i + 1])\n"
        "    return shifted\n"
    )
    (root / "sample.py").write_text(old_source, encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "import pytest\n"
        "from sample import shift_left\n\n"
        "@pytest.mark.parametrize(\n"
        "    'values, expected',\n"
        "    [pytest.param([1], [], id='pkg::empty value')],\n"
        ")\n"
        "def test_shift_left(values, expected):\n"
        "    assert shift_left(values) == expected\n",
        encoding="utf-8",
    )
    return old_source, new_source


def _write_regression_validation_repo(root):
    old_source = (
        "def classify(value):\n"
        "    if value == 0:\n"
        "        return 'zero'\n"
        "    return 'small'\n"
    )
    new_source = (
        "def classify(value):\n"
        "    if value == 0:\n"
        "        return 'zero'\n"
        "    return 'large'\n"
    )
    (root / "sample.py").write_text(old_source, encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "from sample import classify\n\n"
        "def test_large_value():\n"
        "    assert classify(10) == 'large'\n\n"
        "def test_small_value_regression():\n"
        "    assert classify(1) == 'small'\n",
        encoding="utf-8",
    )
    return old_source, new_source


def _write_unchanged_baseline_failure_repo(root):
    old_source = (
        "def classify(value):\n"
        "    if value == 0:\n"
        "        return 'zero'\n"
        "    return 'small'\n"
    )
    new_source = (
        "def classify(value):\n"
        "    if value == 0:\n"
        "        return 'zero'\n"
        "    if value >= 10:\n"
        "        return 'large'\n"
        "    return 'small'\n"
    )
    (root / "sample.py").write_text(old_source, encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "from sample import classify\n\n"
        "def test_large_value():\n"
        "    assert classify(10) == 'large'\n",
        encoding="utf-8",
    )
    (tests / "test_unrelated.py").write_text(
        "def test_existing_unrelated_failure():\n"
        "    try:\n"
        "        return 'unrelated'\n"
        "    except TypeError, ValueError:\n"
        "        return 'legacy syntax'\n",
        encoding="utf-8",
    )
    return old_source, new_source


def _dynamic_evidence():
    return {
        "status": "pass",
        "evidence_level": "failing_tests",
        "usable_for_localization": True,
        "recommended_validation_command": (
            "python -m pytest -q "
            "tests/test_sample.py::test_shift_left_short_and_many"
        ),
        "failure_category": "test_assertion_failure",
        "failure_signal": (
            "FAILED tests/test_sample.py::test_shift_left_short_and_many"
        ),
        "diagnostic_summary": "IndexError from failing repository test.",
        "failing_test_count": 1,
        "failing_tests": [
            {
                "nodeid": "tests/test_sample.py::test_shift_left_short_and_many",
                "path": "tests/test_sample.py",
                "test_name": "test_shift_left_short_and_many",
                "source_line": (
                    "FAILED tests/test_sample.py::test_shift_left_short_and_many"
                    " - IndexError"
                ),
            }
        ],
    }
