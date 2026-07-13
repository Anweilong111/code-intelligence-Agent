from pathlib import Path
import json

from code_intelligence_agent.agents.llm_client import LLMRequestError, StaticLLMClient
from code_intelligence_agent.agents.llm_patch_generator import LLMPatchGenerator
from code_intelligence_agent.evaluation.repository_test_fault_localization import (
    build_repository_test_fault_localization,
)
from code_intelligence_agent.evaluation.repository_test_dynamic_evidence import (
    build_repository_test_dynamic_evidence,
)
from code_intelligence_agent.evaluation.repository_test_patch_candidates import (
    build_repository_test_patch_candidates,
    pytest_args_from_python_module_command,
    render_repository_test_patch_candidates_markdown,
    write_repository_test_patch_candidates_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_patch_validation import (
    build_repository_test_patch_validation,
)


def test_repository_test_patch_candidates_generate_from_fault_localization(tmp_path):
    _write_patch_candidate_repo(tmp_path)
    localization = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=tmp_path,
        top_k=3,
    )
    payload = build_repository_test_patch_candidates(
        localization,
        repository_root=tmp_path,
        candidate_limit=5,
    )
    paths = write_repository_test_patch_candidates_artifacts(
        payload,
        tmp_path / "out",
    )
    markdown = render_repository_test_patch_candidates_markdown(payload)

    assert localization["status"] == "pass"
    assert localization["top_function"] == "shift_left"
    assert payload["status"] == "pass"
    assert payload["reason"] == "patch_candidates_generated"
    assert payload["candidate_count"] == 2
    assert payload["patch_generation_mode"] == "rule"
    assert payload["generator_counts"] == {"rule": 2, "llm": 0}
    assert payload["llm_generation_status"] == "disabled"
    assert payload["safety_gate"]["status"] == "pass"
    assert payload["safety_gate"]["passed_count"] == 2
    assert payload["safety_gate"]["blocked_count"] == 0
    assert payload["target_function_count"] >= 1
    assert payload["recommended_pytest_args"] == [
        "tests/test_sample.py::test_shift_left_short"
    ]
    assert payload["recommended_pytest_args_source"] == "validation_command"
    assert payload["candidate_rule_counts"] == {"possible_index_overrun": 2}
    assert payload["candidates"][0]["rule_id"] == "possible_index_overrun"
    assert payload["candidates"][0]["target_function_name"] == "shift_left"
    assert payload["candidates"][0]["metadata"]["validation"]["valid"] is True
    assert payload["candidates"][0]["metadata"]["safety_gate"]["status"] == "pass"
    assert "range(len(values) - 1)" in payload["candidates"][0]["new_source"]
    assert "Repository Test Patch Candidates" in markdown
    assert "possible_index_overrun" in markdown
    assert "Safety Gate" in markdown
    assert Path(paths["repository_test_patch_candidates_json"]).exists()
    assert Path(paths["repository_test_patch_candidates_markdown"]).exists()


def test_repository_test_patch_candidates_filters_by_variant_allowlist(tmp_path):
    _write_patch_candidate_repo(tmp_path)
    localization = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=tmp_path,
        top_k=3,
    )

    payload = build_repository_test_patch_candidates(
        localization,
        repository_root=tmp_path,
        candidate_limit=5,
        candidate_variant_allowlist=["overly_conservative_range_bound"],
    )
    markdown = render_repository_test_patch_candidates_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["candidate_count"] == 1
    assert payload["candidate_variant_filter"] == {
        "enabled": True,
        "allowlist": ["overly_conservative_range_bound"],
        "input_count": 2,
        "kept_count": 1,
        "dropped_count": 1,
        "dropped_variant_counts": {"shrink_range_upper_bound": 1},
    }
    assert payload["candidates"][0]["metadata"]["variant"] == (
        "overly_conservative_range_bound"
    )
    assert "Candidate Variant Filter: enabled=true" in markdown
    assert "allowlist=`overly_conservative_range_bound`" in markdown


def test_repository_test_patch_candidates_derives_pytest_args_from_unittest_nodeid(
    tmp_path,
):
    _write_unittest_patch_candidate_repo(tmp_path)
    evidence = build_repository_test_dynamic_evidence(
        {
            "status": "fail",
            "executed": True,
            "reason": "command_returncode",
            "command": "python -m unittest discover",
            "returncode": 1,
            "passed": 0,
            "failed": 1,
            "failure_category": "test_assertion_failure",
            "failure_signal": (
                "ERROR: test_shift_left_short "
                "(tests.test_sample.TestShift.test_shift_left_short)"
            ),
            "diagnostic_summary": "A unittest assertion failed.",
            "stdout_preview": "",
            "stderr_preview": "",
        }
    )
    localization = build_repository_test_fault_localization(
        evidence,
        repository_root=tmp_path,
        top_k=3,
    )
    payload = build_repository_test_patch_candidates(
        localization,
        repository_root=tmp_path,
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        payload,
        repository_root=tmp_path,
        validation_limit=2,
    )
    markdown = render_repository_test_patch_candidates_markdown(payload)

    assert localization["status"] == "pass"
    assert localization["matched_failed_test_count"] == 1
    assert localization["recommended_validation_command"] == (
        "python -m unittest discover"
    )
    assert payload["status"] == "pass"
    assert payload["recommended_pytest_args"] == [
        "tests/test_sample.py::TestShift::test_shift_left_short"
    ]
    assert payload["recommended_pytest_args_source"] == "dynamic_evidence_nodeids"
    assert validation["status"] == "pass"
    assert validation["repair_ready"] is True
    assert validation["recommended_pytest_args"] == [
        "tests/test_sample.py::TestShift::test_shift_left_short"
    ]
    assert "Recommended Pytest Args Source: `dynamic_evidence_nodeids`" in markdown


def test_repository_test_patch_candidates_hybrid_adds_llm_candidates(
    tmp_path,
    monkeypatch,
):
    for name in _LLM_API_KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    _write_patch_candidate_repo(tmp_path)
    localization = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=tmp_path,
        top_k=3,
    )
    fixed_source = (
        "def shift_left(values):\n"
        "    return values[1:]\n"
    )
    llm = LLMPatchGenerator(StaticLLMClient(json.dumps({"fixed_source": fixed_source})))

    payload = build_repository_test_patch_candidates(
        localization,
        repository_root=tmp_path,
        candidate_limit=3,
        patch_generation_mode="hybrid",
        llm_generator=llm,
    )
    markdown = render_repository_test_patch_candidates_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["patch_generation_mode"] == "hybrid"
    assert payload["candidate_count"] == 3
    assert payload["generator_counts"] == {"rule": 2, "llm": 1}
    assert payload["llm_generation_status"] == "pass"
    assert payload["llm_generation_reason"] == "llm_patch_candidates_generated"
    assert payload["llm_generation_telemetry"]["request_count"] == 1
    assert payload["llm_generation_telemetry"]["success_count"] == 1
    assert payload["llm_generation_telemetry"]["failure_count"] == 0
    assert len(payload["llm_generation_audit"]) == 1
    assert payload["llm_generation_audit"][0]["requested_candidate_count"] == 1
    assert payload["llm_generation_audit"][0]["parsed_candidate_count"] == 1
    assert payload["llm_generation_audit"][0]["accepted_candidate_count"] == 1
    assert (
        payload["llm_generation_audit"][0]["prompt_context_audit"][
            "required_fields"
        ]["failing_test_nodeid"]
        is True
    )
    assert payload["llm_repair_context"]["dynamic_evidence_level"] == (
        "failing_tests"
    )
    assert payload["llm_repair_context"]["recommended_validation_command"] == (
        "python -m pytest -q tests/test_sample.py::test_shift_left_short"
    )
    prompt_payload = json.loads(llm.client.prompts[0])
    assert prompt_payload["dynamic_oracle"]["dynamic_evidence_level"] == (
        "failing_tests"
    )
    assert prompt_payload["dynamic_oracle"]["recommended_validation_command"] == (
        "python -m pytest -q tests/test_sample.py::test_shift_left_short"
    )
    assert payload["llm_config_audit"]["enabled"] is True
    assert payload["llm_config_audit"]["api_key_present"] is False
    assert payload["safety_gate"]["status"] == "pass"
    assert payload["candidate_rule_counts"] == {
        "llm_patch": 1,
        "possible_index_overrun": 2,
    }
    llm_candidates = [
        row for row in payload["candidates"]
        if row["metadata"].get("generator") == "llm"
    ]
    assert len(llm_candidates) == 1
    assert llm_candidates[0]["metadata"]["validation"]["valid"] is True
    assert llm_candidates[0]["metadata"]["safety_gate"]["status"] == "pass"
    assert llm_candidates[0]["metadata"]["response_parse"]["status"] == "pass"
    assert llm_candidates[0]["metadata"]["prompt_context_audit"][
        "candidate_count_requested"
    ] == 1
    assert "Patch Generation Mode: `hybrid`" in markdown
    assert "llm=1" in markdown
    assert "LLM Telemetry: requests=1" in markdown
    assert "LLM Generation Audit" in markdown


def test_repository_test_patch_candidates_llm_reads_session_patch_memory(
    tmp_path,
    monkeypatch,
):
    for name in _LLM_API_KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    _write_patch_candidate_repo(tmp_path)
    localization = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=tmp_path,
        top_k=3,
    )
    memory_path = tmp_path / "agent_memory.json"
    memory_path.write_text(
        json.dumps(
            {
                "patch_attempt_history": [
                    {
                        "candidate_id": "bad_patch_1",
                        "target_function": "sample.shift_left",
                        "failure_type": "assertion_failure",
                        "sandbox_status": "fail",
                        "passed": False,
                        "diff_fingerprint": "failed-diff-fp",
                    }
                ],
                "constraints": ["不要修改公共 API"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CIA_AGENT_PATCH_MEMORY", str(memory_path))
    monkeypatch.setenv("CIA_AGENT_REPAIR_STRATEGY", "prefer guard clause")
    fixed_source = (
        "def shift_left(values):\n"
        "    return values[1:]\n"
    )
    llm = LLMPatchGenerator(StaticLLMClient(json.dumps({"fixed_source": fixed_source})))

    payload = build_repository_test_patch_candidates(
        localization,
        repository_root=tmp_path,
        candidate_limit=3,
        patch_generation_mode="hybrid",
        llm_generator=llm,
    )

    prompt_payload = json.loads(llm.client.prompts[0])
    assert payload["llm_repair_context"]["previous_failed_patch_fingerprints"] == [
        "failed-diff-fp"
    ]
    assert (
        payload["llm_repair_context"]["session_patch_memory"]["failed_patch_count"]
        == 1
    )
    assert prompt_payload["previous_failed_patch_fingerprints"] == [
        "failed-diff-fp"
    ]
    assert payload["llm_repair_context"]["repair_strategy_preferences"] == [
        "prefer guard clause"
    ]
    assert (
        prompt_payload["dynamic_oracle"]["session_patch_memory"][
            "failed_patch_summaries"
        ][0]["candidate_id"]
        == "bad_patch_1"
    )
    assert "User constraint: 不要修改公共 API" in prompt_payload["constraints"]
    assert any(
        "session_patch_memory" in constraint
        for constraint in prompt_payload["constraints"]
    )
    assert any(
        "Repair strategy preference: prefer guard clause" in constraint
        for constraint in prompt_payload["constraints"]
    )


def test_repository_test_patch_candidates_llm_mode_blocks_without_api_key(
    tmp_path,
    monkeypatch,
):
    for name in _LLM_API_KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    _write_patch_candidate_repo(tmp_path)
    localization = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=tmp_path,
        top_k=3,
    )

    payload = build_repository_test_patch_candidates(
        localization,
        repository_root=tmp_path,
        candidate_limit=3,
        patch_generation_mode="llm",
    )

    assert payload["status"] == "warning"
    assert payload["reason"] == "no_patch_candidates_generated"
    assert payload["candidate_count"] == 0
    assert payload["generator_counts"] == {"rule": 0, "llm": 0}
    assert payload["llm_generation_status"] == "blocked"
    assert payload["llm_generation_reason"] == "missing_llm_api_key"
    assert payload["llm_config_audit"]["enabled"] is True
    assert payload["llm_config_audit"]["api_key_present"] is False
    assert payload["safety_gate"]["candidate_count"] == 0


def test_repository_test_patch_candidates_records_llm_request_error_telemetry(
    tmp_path,
):
    _write_patch_candidate_repo(tmp_path)
    localization = build_repository_test_fault_localization(
        _dynamic_evidence(),
        repository_root=tmp_path,
        top_k=3,
    )

    payload = build_repository_test_patch_candidates(
        localization,
        repository_root=tmp_path,
        candidate_limit=2,
        patch_generation_mode="llm",
        llm_generator=_FailingLLMGenerator(),
    )

    assert payload["status"] == "warning"
    assert payload["candidate_count"] == 0
    assert payload["llm_generation_status"] == "error"
    assert payload["llm_generation_reason"] == "http_error"
    assert payload["generation_errors"][0]["reason"] == "http_error"
    assert payload["generation_errors"][0]["llm_request_metadata"]["status"] == (
        "error"
    )
    assert payload["llm_generation_telemetry"]["request_count"] == 1
    assert payload["llm_generation_telemetry"]["failure_count"] == 1
    assert payload["llm_generation_telemetry"]["providers"] == ["deepseek"]
    assert payload["llm_generation_telemetry"]["models"] == ["deepseek-v4-pro"]
    assert payload["llm_generation_telemetry"]["error_reason_counts"] == {
        "http_401": 1,
    }


def test_repository_test_patch_candidates_skip_when_localization_not_ready(tmp_path):
    payload = build_repository_test_patch_candidates(
        {
            "status": "skipped",
            "reason": "dynamic_evidence_not_usable",
            "ranking_count": 0,
        },
        repository_root=tmp_path,
        patch_generation_mode="hybrid",
        llm_candidate_limit=2,
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "fault_localization_not_ready"
    assert payload["candidate_count"] == 0
    assert payload["patch_generation_mode"] == "hybrid"
    assert payload["generator_counts"] == {"rule": 0, "llm": 0}
    assert payload["llm_generation_status"] == "blocked"
    assert payload["llm_generation_reason"] == "fault_localization_not_ready"
    assert payload["llm_candidate_limit"] == 2
    assert payload["llm_config_audit"]["enabled"] is True
    assert payload["safety_gate"]["status"] == "skipped"
    assert payload["next_actions"]


def test_pytest_args_from_python_module_command_extracts_nodeids():
    assert pytest_args_from_python_module_command(
        "python -m pytest -q tests/test_sample.py::test_one"
    ) == ["tests/test_sample.py::test_one"]
    assert pytest_args_from_python_module_command("pytest -q tests") == []


def test_pytest_args_from_python_module_command_preserves_parameterized_nodeids():
    assert pytest_args_from_python_module_command(
        "py -m pytest -q --maxfail=1 "
        "'tests/test_sample.py::test_shift_left[pkg::empty value]'"
    ) == [
        "--maxfail=1",
        "tests/test_sample.py::test_shift_left[pkg::empty value]",
    ]
    assert pytest_args_from_python_module_command(
        'python3 -m pytest --quiet "tests/test_sample.py::test_shift_left[empty value]"'
    ) == ["tests/test_sample.py::test_shift_left[empty value]"]


def _write_patch_candidate_repo(root):
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
        "def test_shift_left_short():\n"
        "    assert shift_left([1]) == []\n",
        encoding="utf-8",
    )


def _write_unittest_patch_candidate_repo(root):
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
    (tests / "__init__.py").write_text("", encoding="utf-8")
    (tests / "test_sample.py").write_text(
        "import unittest\n"
        "from sample import shift_left\n\n"
        "class TestShift(unittest.TestCase):\n"
        "    def test_shift_left_short(self):\n"
        "        self.assertEqual(shift_left([1]), [])\n",
        encoding="utf-8",
    )


_LLM_API_KEY_ENV_NAMES = (
    "CIA_LLM_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "ALIBABA_API_KEY",
)


class _FailingLLMGenerator:
    def generate(self, *args, **kwargs):
        del args, kwargs
        raise LLMRequestError(
            "http_error",
            "LLM request failed with HTTP 401.",
            {
                "status": "error",
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "latency_ms": 9,
                "usage": {
                    "estimated_total_tokens": 8,
                },
                "cost_estimate": {
                    "available": False,
                    "estimated_cost_usd": None,
                },
                "error_reason": "http_401",
            },
        )


def _dynamic_evidence():
    return {
        "status": "pass",
        "evidence_level": "failing_tests",
        "usable_for_localization": True,
        "recommended_validation_command": (
            "python -m pytest -q tests/test_sample.py::test_shift_left_short"
        ),
        "failure_category": "test_assertion_failure",
        "failure_signal": "FAILED tests/test_sample.py::test_shift_left_short",
        "diagnostic_summary": "IndexError from failing repository test.",
        "failing_test_count": 1,
        "failing_tests": [
            {
                "nodeid": "tests/test_sample.py::test_shift_left_short",
                "path": "tests/test_sample.py",
                "test_name": "test_shift_left_short",
                "source_line": (
                    "FAILED tests/test_sample.py::test_shift_left_short - IndexError"
                ),
            }
        ],
    }
