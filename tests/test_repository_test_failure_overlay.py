from pathlib import Path

import pytest

from code_intelligence_agent.evaluation.repository_test_failure_overlay import (
    build_repository_test_failure_overlay as _build_repository_test_failure_overlay,
    render_repository_test_failure_overlay_markdown,
    write_repository_test_failure_overlay_artifacts,
)
from code_intelligence_agent.evaluation.repository_test_fault_localization import (
    build_repository_test_fault_localization,
)
from code_intelligence_agent.evaluation.repository_test_patch_candidates import (
    build_repository_test_patch_candidates,
)
from code_intelligence_agent.evaluation.repository_test_patch_validation import (
    build_repository_test_patch_validation as _build_repository_test_patch_validation,
)


pytestmark = [pytest.mark.overlay, pytest.mark.slow]

_REPOSITORY_TEST_SUBPROCESS_TIMEOUT = 20


def build_repository_test_failure_overlay(*args, **kwargs):
    timeout = kwargs.get("timeout")
    if timeout is None or timeout < _REPOSITORY_TEST_SUBPROCESS_TIMEOUT:
        kwargs["timeout"] = _REPOSITORY_TEST_SUBPROCESS_TIMEOUT
    return _build_repository_test_failure_overlay(*args, **kwargs)


def build_repository_test_patch_validation(*args, **kwargs):
    timeout = kwargs.get("timeout")
    if timeout is None or timeout < _REPOSITORY_TEST_SUBPROCESS_TIMEOUT:
        kwargs["timeout"] = _REPOSITORY_TEST_SUBPROCESS_TIMEOUT
    return _build_repository_test_patch_validation(*args, **kwargs)


def test_repository_test_failure_overlay_generates_dynamic_evidence(tmp_path):
    _write_index_overrun_repo(tmp_path)
    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    paths = write_repository_test_failure_overlay_artifacts(payload, tmp_path / "out")
    markdown = render_repository_test_failure_overlay_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["reason"] == "overlay_dynamic_evidence_generated"
    assert payload["selected_case"]["rule_id"] == "possible_index_overrun"
    assert payload["selected_case"]["function_name"] == "shift_left"
    assert payload["dynamic_evidence"]["usable_for_localization"] is True
    assert payload["dynamic_evidence"]["failing_test_count"] == 1
    assert payload["recommended_validation_command"].startswith("python -m pytest -q")
    assert payload["strategy_summary"]["policy"] == (
        "rule_diverse_confidence_ordered_first_triggering_candidate"
    )
    assert payload["selected_case"]["overlay_score"] > 0.0
    assert payload["selected_case"]["score_breakdown"]["static_confidence"] > 0.0
    assert payload["selected_case"]["score_breakdown"]["rule_trigger_prior"] > 0.0
    assert payload["strategy_summary"]["selected_score"] == payload[
        "selected_case"
    ]["overlay_score"]
    assert payload["strategy_summary"]["average_candidate_score"] > 0.0
    assert payload["strategy_summary"]["candidate_score_preview"][0][
        "overlay_score"
    ] > 0.0
    assert payload["strategy_summary"]["candidate_rule_counts"][
        "possible_index_overrun"
    ] >= 1
    assert payload["strategy_summary"]["attempted_rule_counts"][
        "possible_index_overrun"
    ] >= 1
    assert payload["strategy_summary"]["triggered_rule_counts"][
        "possible_index_overrun"
    ] >= 1
    assert payload["strategy_summary"]["selected_candidate_rank"] >= 1
    assert "Repository Test Failure Overlay" in markdown
    assert "Strategy Summary" in markdown
    assert "Candidate Rule Counts" in markdown
    assert "Candidate Score Preview" in markdown
    assert "Overlay Score" in markdown
    assert "possible_index_overrun" in markdown
    assert Path(payload["overlay_root"]).exists()
    assert Path(paths["repository_test_failure_overlay_json"]).exists()
    assert Path(paths["repository_test_failure_overlay_markdown"]).exists()


def test_repository_test_failure_overlay_supports_identity_literal_comparison(
    tmp_path,
):
    _write_identity_literal_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "identity_comparison_literal"
    assert overlay["selected_case"]["function_name"] == "is_admin"
    assert overlay["selected_case"]["call_args"] == ["__cia_value"]
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert "literal equality semantics" in "\n".join(
        overlay["selected_case"]["assertion_lines"]
    )
    assert localization["status"] == "pass"
    assert localization["top_function"] == "is_admin"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_nested_identity_literal_comparison(
    tmp_path,
):
    _write_nested_identity_literal_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "identity_comparison_literal"
    assert overlay["selected_case"]["function_name"] == "is_admin_token.compare_token"
    assert overlay["selected_case"]["callable_kind"] == "nested_identity_literal_function"
    assert overlay["selected_case"]["call_target"] == "is_admin_token"
    assert overlay["selected_case"]["call_args"] == ["__cia_value"]
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert "nested literal equality semantics" in "\n".join(
        overlay["selected_case"]["assertion_lines"]
    )
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["identity_comparison_literal"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_nested_identity_literal_comparison(
    tmp_path,
):
    _write_method_nested_identity_literal_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "identity_comparison_literal"
    assert overlay["selected_case"]["function_name"] == (
        "TokenPolicy.is_admin_token.compare_token"
    )
    assert overlay["selected_case"]["callable_kind"] == "nested_identity_literal_method"
    assert overlay["selected_case"]["call_target"] == "__cia_instance.is_admin_token"
    assert overlay["selected_case"]["setup_lines"][:1] == [
        "__cia_instance = TokenPolicy()"
    ]
    assert overlay["selected_case"]["call_args"] == ["__cia_value"]
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["identity_comparison_literal"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_enumerate_start_zero_counter(
    tmp_path,
):
    _write_enumerate_start_zero_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "enumerate_start_zero_counter"
    assert overlay["selected_case"]["function_name"] == "numbered"
    assert overlay["selected_case"]["call_args"] == ["__cia_sequence"]
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert "one-based enumerate counter" in "\n".join(
        overlay["selected_case"]["assertion_lines"]
    )
    assert localization["status"] == "pass"
    assert localization["top_function"] == "numbered"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_enumerate_average_container(
    tmp_path,
):
    _write_enumerate_average_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "enumerate_start_zero_counter"
    assert overlay["selected_case"]["function_name"] == "iterator_average.count_items"
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_generator_average_function"
    )
    assert overlay["selected_case"]["call_target"] == "iterator_average"
    assert overlay["selected_case"]["call_args"] == ["__cia_one_item()"]
    assert overlay["selected_case"]["expected_exception"] == "ZeroDivisionError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["enumerate_start_zero_counter"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_enumerate_average_container(
    tmp_path,
):
    _write_method_enumerate_average_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "enumerate_start_zero_counter"
    assert overlay["selected_case"]["function_name"] == (
        "AverageCounter.iterator_average.count_items"
    )
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_generator_average_method"
    )
    assert overlay["selected_case"]["call_target"] == (
        "__cia_instance.iterator_average"
    )
    assert overlay["selected_case"]["setup_lines"][:1] == [
        "__cia_instance = AverageCounter()"
    ]
    assert overlay["selected_case"]["call_args"] == ["__cia_one_item()"]
    assert overlay["selected_case"]["expected_exception"] == "ZeroDivisionError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["enumerate_start_zero_counter"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_nested_missing_len_helper(
    tmp_path,
):
    _write_nested_missing_len_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "missing_len_zero_guard"
    assert overlay["selected_case"]["function_name"] == "average_value.average_core"
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_missing_len_guard_function"
    )
    assert overlay["selected_case"]["call_target"] == "average_value"
    assert overlay["selected_case"]["call_args"] == ["[]"]
    assert overlay["selected_case"]["expected_exception"] == "ZeroDivisionError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["missing_len_zero_guard"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_nested_missing_len_helper(
    tmp_path,
):
    _write_method_nested_missing_len_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "missing_len_zero_guard"
    assert overlay["selected_case"]["function_name"] == (
        "Stats.average_value.average_core"
    )
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_missing_len_guard_method"
    )
    assert overlay["selected_case"]["call_target"] == (
        "__cia_instance.average_value"
    )
    assert overlay["selected_case"]["setup_lines"][:1] == ["__cia_instance = Stats()"]
    assert overlay["selected_case"]["call_args"] == ["[]"]
    assert overlay["selected_case"]["expected_exception"] == "ZeroDivisionError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["missing_len_zero_guard"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_inverted_empty_guard(
    tmp_path,
):
    _write_inverted_empty_guard_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "inverted_empty_guard"
    assert overlay["selected_case"]["function_name"] == "mean_value"
    assert overlay["selected_case"]["call_args"] == ["[1, 2, 3]"]
    assert overlay["selected_case"]["expected_exception"] == "ValueError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["inverted_empty_guard"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_always_true_len_check(
    tmp_path,
):
    _write_always_true_len_check_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "always_true_len_check"
    assert overlay["selected_case"]["function_name"] == "require_scheme"
    assert overlay["selected_case"]["call_args"] == ['""']
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert overlay["selected_case"]["success_exception"] == "ValueError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["always_true_len_check"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_always_true_len_check(
    tmp_path,
):
    _write_method_always_true_len_check_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "always_true_len_check"
    assert overlay["selected_case"]["function_name"] == "UrlRules.require_scheme"
    assert overlay["selected_case"]["callable_kind"] == "method"
    assert overlay["selected_case"]["setup_lines"][:1] == ["__cia_instance = UrlRules()"]
    assert overlay["selected_case"]["call_args"] == ['""']
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert overlay["selected_case"]["success_exception"] == "ValueError"


def test_repository_test_failure_overlay_supports_nested_always_true_len_check(
    tmp_path,
):
    _write_nested_always_true_len_check_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "always_true_len_check"
    assert overlay["selected_case"]["function_name"] == "require_scheme.validate_scheme"
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_always_true_len_check_function"
    )
    assert overlay["selected_case"]["call_target"] == "require_scheme"
    assert overlay["selected_case"]["call_args"] == ['""']
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert overlay["selected_case"]["success_exception"] == "ValueError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["always_true_len_check"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_nested_always_true_len_check(
    tmp_path,
):
    _write_method_nested_always_true_len_check_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "always_true_len_check"
    assert overlay["selected_case"]["function_name"] == (
        "UrlRules.require_scheme.validate_scheme"
    )
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_always_true_len_check_method"
    )
    assert overlay["selected_case"]["call_target"] == "__cia_instance.require_scheme"
    assert overlay["selected_case"]["setup_lines"][:1] == ["__cia_instance = UrlRules()"]
    assert overlay["selected_case"]["call_args"] == ['""']
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert overlay["selected_case"]["success_exception"] == "ValueError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["always_true_len_check"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_nested_inverted_empty_guard(
    tmp_path,
):
    _write_nested_inverted_empty_guard_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "inverted_empty_guard"
    assert overlay["selected_case"]["function_name"] == "mean_value.mean_core"
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_inverted_empty_guard_function"
    )
    assert overlay["selected_case"]["call_target"] == "mean_value"
    assert overlay["selected_case"]["call_args"] == ["[1, 2, 3]"]
    assert overlay["selected_case"]["expected_exception"] == "ValueError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["inverted_empty_guard"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_nested_inverted_empty_guard(
    tmp_path,
):
    _write_method_nested_inverted_empty_guard_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "inverted_empty_guard"
    assert overlay["selected_case"]["function_name"] == "Stats.mean_value.mean_core"
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_inverted_empty_guard_method"
    )
    assert overlay["selected_case"]["call_target"] == "__cia_instance.mean_value"
    assert overlay["selected_case"]["setup_lines"][:1] == ["__cia_instance = Stats()"]
    assert overlay["selected_case"]["call_args"] == ["[1, 2, 3]"]
    assert overlay["selected_case"]["expected_exception"] == "ValueError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["inverted_empty_guard"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_nested_dict_missing_key_helper(
    tmp_path,
):
    _write_nested_dict_missing_key_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "dict_missing_key_guard"
    assert overlay["selected_case"]["function_name"] == "score_for.lookup_score"
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_dict_missing_key_function"
    )
    assert overlay["selected_case"]["call_target"] == "score_for"
    assert overlay["selected_case"]["call_args"] == ["{}", "'__cia_missing_key__'"]
    assert overlay["selected_case"]["expected_exception"] == "KeyError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["dict_missing_key_guard"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_nested_dict_missing_key_helper(
    tmp_path,
):
    _write_method_nested_dict_missing_key_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "dict_missing_key_guard"
    assert overlay["selected_case"]["function_name"] == (
        "ScoreBoard.score_for.lookup_score"
    )
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_dict_missing_key_method"
    )
    assert overlay["selected_case"]["call_target"] == "__cia_instance.score_for"
    assert overlay["selected_case"]["setup_lines"][:1] == [
        "__cia_instance = ScoreBoard()"
    ]
    assert overlay["selected_case"]["call_args"] == ["{}", "'__cia_missing_key__'"]
    assert overlay["selected_case"]["expected_exception"] == "KeyError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["dict_missing_key_guard"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_scopes_static_analysis_paths(tmp_path):
    (tmp_path / "selected.py").write_text(
        "def selected_shift(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values)):\n"
        "        shifted.append(values[i + 1])\n"
        "    return shifted\n",
        encoding="utf-8",
    )
    (tmp_path / "ignored.py").write_text(
        "def ignored_shift(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values)):\n"
        "        shifted.append(values[i + 1])\n"
        "    return shifted\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
        analysis_paths=["selected.py", "missing.py"],
    )
    markdown = render_repository_test_failure_overlay_markdown(payload)

    assert payload["status"] == "pass"
    assert payload["analysis_scope"]["enabled"] is True
    assert payload["analysis_scope"]["existing_files"] == ["selected.py"]
    assert payload["analysis_scope"]["missing_paths"] == ["missing.py"]
    assert payload["static_finding_count"] == 1
    assert payload["selected_case"]["relative_file_path"] == "selected.py"
    assert payload["selected_case"]["function_name"] == "selected_shift"
    assert "ignored_shift" not in str(payload)
    assert "Scoped Analysis: true" in markdown
    assert "Analysis Files" in markdown


def test_repository_test_failure_overlay_prefers_rule_diverse_candidates(tmp_path):
    _write_rule_diverse_overlay_repo(tmp_path)
    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
        candidate_limit=2,
    )

    assert payload["status"] == "pass"
    assert payload["strategy_summary"]["candidate_limit"] == 2
    assert payload["strategy_summary"]["candidate_rule_counts"] == {
        "inplace_api_return_value": 1,
        "possible_index_overrun": 1,
    }
    assert payload["supported_candidate_count"] == 2


def test_repository_test_failure_overlay_feeds_patch_validation(tmp_path):
    _write_index_overrun_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "shift_left"
    assert candidates["status"] == "pass"
    assert candidates["candidate_count"] == 2
    assert candidates["recommended_pytest_args"] == [
        "tests/test_cia_overlay_possible_index_overrun.py::test_cia_overlay_shift_left_possible_index_overrun"
    ]
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1
    assert validation["recommended_pytest_args"] == candidates["recommended_pytest_args"]


def test_repository_test_failure_overlay_supports_no_arg_class_method(tmp_path):
    _write_method_index_overrun_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "method"
    assert overlay["selected_case"]["class_name"] == "Window"
    assert overlay["selected_case"]["function_name"] == "Window.shift_left"
    assert overlay["selected_case"]["call_target"] == "__cia_instance.shift_left"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "Window.shift_left"
    assert candidates["status"] == "pass"
    assert candidates["recommended_pytest_args"] == [
        "tests/test_cia_overlay_possible_index_overrun.py::test_cia_overlay_window_shift_left_possible_index_overrun"
    ]
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_nested_index_overrun_function(
    tmp_path,
):
    _write_nested_index_overrun_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "possible_index_overrun"
    assert overlay["selected_case"]["function_name"] == "shift_left.shift_core"
    assert overlay["selected_case"]["callable_kind"] == "nested_index_overrun_function"
    assert overlay["selected_case"]["call_target"] == "shift_left"
    assert overlay["selected_case"]["call_args"] == ["[1]"]
    assert overlay["selected_case"]["expected_exception"] == "IndexError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["possible_index_overrun"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_nested_index_overrun(
    tmp_path,
):
    _write_method_nested_index_overrun_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "possible_index_overrun"
    assert overlay["selected_case"]["function_name"] == "Window.shift_left.shift_core"
    assert overlay["selected_case"]["callable_kind"] == "nested_index_overrun_method"
    assert overlay["selected_case"]["call_target"] == "__cia_instance.shift_left"
    assert overlay["selected_case"]["setup_lines"][:1] == ["__cia_instance = Window()"]
    assert overlay["selected_case"]["call_args"] == ["[1]"]
    assert overlay["selected_case"]["expected_exception"] == "IndexError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["possible_index_overrun"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_staticmethod(tmp_path):
    _write_staticmethod_index_overrun_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "staticmethod"
    assert overlay["selected_case"]["function_name"] == "Window.shift_left"
    assert overlay["selected_case"]["call_target"] == "Window.shift_left"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "Window.shift_left"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_classmethod(tmp_path):
    _write_classmethod_index_overrun_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "classmethod"
    assert overlay["selected_case"]["function_name"] == "Window.shift_left"
    assert overlay["selected_case"]["call_target"] == "Window.shift_left"
    assert overlay["selected_case"]["call_args"] == ["[1]"]
    assert localization["status"] == "pass"
    assert localization["top_function"] == "Window.shift_left"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_identity_decorated_method(tmp_path):
    _write_identity_decorated_method_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "decorated_identity_method"
    assert overlay["selected_case"]["function_name"] == "Window.shift_left"
    assert overlay["selected_case"]["call_target"] == "__cia_instance.shift_left"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "Window.shift_left"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_transparent_wrapper_decorated_method(
    tmp_path,
):
    _write_transparent_wrapper_decorated_method_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert (
        overlay["selected_case"]["callable_kind"]
        == "decorated_transparent_wrapper_method"
    )
    assert overlay["selected_case"]["function_name"] == "Window.shift_left"
    assert overlay["selected_case"]["call_target"] == "__cia_instance.shift_left"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "Window.shift_left"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_returned_nested_function_factory(
    tmp_path,
):
    _write_returned_nested_function_factory_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "returned_nested_function"
    assert overlay["selected_case"]["function_name"] == "outer.shift_left"
    assert overlay["selected_case"]["import_name"] == "outer"
    assert overlay["selected_case"]["call_target"] == "__cia_callable"
    assert overlay["selected_case"]["setup_lines"] == ["__cia_callable = outer()"]
    assert overlay["selected_case"]["call_args"] == ["[1]"]


def test_repository_test_failure_overlay_supports_returned_nested_function_factory_args(
    tmp_path,
):
    _write_returned_nested_function_factory_arg_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "returned_nested_function"
    assert overlay["selected_case"]["function_name"] == "outer.shift_left"
    assert overlay["selected_case"]["setup_lines"] == ["__cia_callable = outer(0)"]
    assert overlay["selected_case"]["call_args"] == ["[1]"]


def test_repository_test_failure_overlay_supports_returned_nested_factory_callable_stub(
    tmp_path,
):
    (tmp_path / "sample.py").write_text(
        "from typing import Callable\n\n"
        "def outer(source: Callable[[], list]):\n"
        "    def shift_left(values):\n"
        "        source()\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n"
        "    return shift_left\n",
        encoding="utf-8",
    )

    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "returned_nested_function"
    assert overlay["selected_case"]["setup_lines"] == [
        "def __cia_factory_source():",
        "    return []",
        "__cia_callable = outer(__cia_factory_source)",
    ]
    assert overlay["selected_case"]["call_args"] == ["[1]"]


def test_repository_test_failure_overlay_supports_returned_nested_factory_textio_stub(
    tmp_path,
):
    (tmp_path / "sample.py").write_text(
        "from typing import Callable, TextIO\n\n"
        "def outer(open_stream: Callable[[], TextIO]):\n"
        "    def shift_left(values):\n"
        "        open_stream().write('')\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n"
        "    return shift_left\n",
        encoding="utf-8",
    )

    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["setup_lines"] == [
        "import io",
        "def __cia_factory_open_stream():",
        "    return io.StringIO()",
        "__cia_callable = outer(__cia_factory_open_stream)",
    ]
    assert overlay["selected_case"]["call_args"] == ["[1]"]


def test_repository_test_failure_overlay_supports_returned_nested_factory_bytes_stub(
    tmp_path,
):
    (tmp_path / "sample.py").write_text(
        "from typing import Callable\n\n"
        "def outer(read_chunk: Callable[[], bytes]):\n"
        "    def shift_left(values):\n"
        "        read_chunk()\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n"
        "    return shift_left\n",
        encoding="utf-8",
    )

    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "returned_nested_function"
    assert overlay["selected_case"]["setup_lines"] == [
        "def __cia_factory_read_chunk():",
        "    return b''",
        "__cia_callable = outer(__cia_factory_read_chunk)",
    ]
    assert overlay["selected_case"]["call_args"] == ["[1]"]
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_returned_nested_factory_collection_and_path_stubs(
    tmp_path,
):
    (tmp_path / "sample.py").write_text(
        "from pathlib import Path\n"
        "from typing import Callable\n\n"
        "def outer(\n"
        "    load_shape: Callable[[], tuple],\n"
        "    load_tags: Callable[[], set],\n"
        "    load_path: Callable[[], Path],\n"
        "):\n"
        "    def shift_left(values):\n"
        "        load_shape()\n"
        "        load_tags()\n"
        "        load_path()\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n"
        "    return shift_left\n",
        encoding="utf-8",
    )

    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "returned_nested_function"
    assert overlay["selected_case"]["setup_lines"] == [
        "def __cia_factory_load_shape():",
        "    return ()",
        "def __cia_factory_load_tags():",
        "    return set()",
        "def __cia_factory_load_path():",
        "    return Path('.')",
        (
            "__cia_callable = outer("
            "__cia_factory_load_shape, __cia_factory_load_tags, "
            "__cia_factory_load_path)"
        ),
    ]
    assert overlay["selected_case"]["call_args"] == ["[1]"]
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_returned_nested_factory_setup_body(
    tmp_path,
):
    (tmp_path / "sample.py").write_text(
        "def outer():\n"
        "    cache = {}\n"
        "    def shift_left(values):\n"
        "        cache['calls'] = cache.get('calls', 0) + 1\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n"
        "    return shift_left\n",
        encoding="utf-8",
    )

    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "returned_nested_function"
    assert overlay["selected_case"]["setup_lines"] == ["__cia_callable = outer()"]
    assert overlay["selected_case"]["call_args"] == ["[1]"]


def test_repository_test_failure_overlay_supports_returned_nested_broad_exception(
    tmp_path,
):
    _write_returned_nested_broad_exception_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "broad_exception_pass"
    assert overlay["selected_case"]["callable_kind"] == "returned_nested_function"
    assert overlay["selected_case"]["function_name"] == "outer.mean_core"
    assert overlay["selected_case"]["import_name"] == "outer"
    assert overlay["selected_case"]["call_target"] == "__cia_callable"
    assert overlay["selected_case"]["setup_lines"] == ["__cia_callable = outer()"]
    assert overlay["selected_case"]["call_args"] == ["[]"]
    assert overlay["selected_case"]["success_exception"] == "ValueError"
    assert overlay["dynamic_evidence"]["public_api_evidence"]["trigger_scope"] == (
        "factory_to_returned_callable"
    )
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["broad_exception_pass"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_contextmanager_method(tmp_path):
    _write_contextmanager_index_overrun_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "contextmanager_method"
    assert overlay["selected_case"]["function_name"] == "Window.shift_left"
    assert overlay["selected_case"]["call_target"] == "__cia_instance.shift_left"
    assert overlay["selected_case"]["call_style"] == "contextmanager"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "Window.shift_left"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_contextmanager_function(tmp_path):
    _write_contextmanager_function_index_overrun_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["callable_kind"] == "contextmanager_function"
    assert overlay["selected_case"]["function_name"] == "shift_left"
    assert overlay["selected_case"]["call_target"] == "shift_left"
    assert overlay["selected_case"]["call_style"] == "contextmanager"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "shift_left"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_reports_contextmanager_oracle_rejection(
    tmp_path,
):
    (tmp_path / "sample.py").write_text(
        "from contextlib import contextmanager\n\n"
        "class Window:\n"
        "    @contextmanager\n"
        "    def risky(self):\n"
        "        try:\n"
        "            self.flush()\n"
        "        except Exception:\n"
        "            pass\n"
        "        yield self.values\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    counts = payload["strategy_summary"]["candidate_rejection_counts"]
    assert payload["status"] == "skipped"
    assert counts["broad_exception_contextmanager_lifecycle_unsupported"] >= 1
    assert "broad_exception_fallback_flow_unsupported" not in counts
    assert "contextmanager_method_unsupported" not in counts
    markdown = render_repository_test_failure_overlay_markdown(payload)
    assert "broad_exception_contextmanager_lifecycle_unsupported" in markdown


def test_repository_test_failure_overlay_supports_inplace_api_function(tmp_path):
    _write_inplace_api_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "inplace_api_return_value"
    assert overlay["selected_case"]["callable_kind"] == "function"
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "sorted_values"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"] == {"inplace_api_return_value": 1}
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_inplace_api_method(tmp_path):
    _write_method_inplace_api_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "inplace_api_return_value"
    assert overlay["selected_case"]["callable_kind"] == "method"
    assert overlay["selected_case"]["function_name"] == "Sorter.sorted_values"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "Sorter.sorted_values"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_nested_inplace_api_function(tmp_path):
    _write_nested_inplace_api_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "inplace_api_return_value"
    assert overlay["selected_case"]["function_name"] == "sorted_values.sort_core"
    assert overlay["selected_case"]["callable_kind"] == "nested_inplace_api_function"
    assert overlay["selected_case"]["call_target"] == "sorted_values"
    assert overlay["selected_case"]["call_args"] == ["[3, 1, 2]"]
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["inplace_api_return_value"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_nested_inplace_api(tmp_path):
    _write_method_nested_inplace_api_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "inplace_api_return_value"
    assert overlay["selected_case"]["function_name"] == "Sorter.sorted_values.sort_core"
    assert overlay["selected_case"]["callable_kind"] == "nested_inplace_api_method"
    assert overlay["selected_case"]["call_target"] == "__cia_instance.sorted_values"
    assert overlay["selected_case"]["setup_lines"][:1] == ["__cia_instance = Sorter()"]
    assert overlay["selected_case"]["call_args"] == ["[3, 1, 2]"]
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["inplace_api_return_value"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_stringified_numeric_function(tmp_path):
    _write_stringified_numeric_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "stringified_numeric_value"
    assert overlay["selected_case"]["callable_kind"] == "function"
    assert overlay["selected_case"]["public_api_evidence"] == {
        "trigger_scope": "direct_function",
        "internal_target": "middle_value",
        "public_entrypoint": "middle_value",
        "public_call_args": ["__cia_sequence"],
        "trigger_expression": "middle_value(__cia_sequence)",
        "call_style": "call",
        "callable_kind": "function",
        "is_nested_target": False,
        "entrypoint_differs_from_internal_target": False,
    }
    assert overlay["selected_case"]["expected_exception"] == "TypeError"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "middle_value"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"] == {"stringified_numeric_value": 1}
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_stringified_numeric_method(tmp_path):
    _write_method_stringified_numeric_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "stringified_numeric_value"
    assert overlay["selected_case"]["callable_kind"] == "method"
    assert overlay["selected_case"]["function_name"] == "WindowPicker.middle_value"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "WindowPicker.middle_value"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_nested_stringified_numeric_helper(
    tmp_path,
):
    _write_nested_stringified_numeric_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "stringified_numeric_value"
    assert overlay["selected_case"]["function_name"] == "middle_value.pick_middle"
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_stringified_numeric_function"
    )
    assert overlay["selected_case"]["call_target"] == "middle_value"
    assert overlay["selected_case"]["call_args"] == ["__cia_sequence"]
    assert overlay["selected_case"]["public_api_evidence"] == {
        "trigger_scope": "public_entrypoint_to_nested_target",
        "internal_target": "middle_value.pick_middle",
        "public_entrypoint": "middle_value",
        "public_call_args": ["__cia_sequence"],
        "trigger_expression": "middle_value(__cia_sequence)",
        "call_style": "call",
        "callable_kind": "nested_stringified_numeric_function",
        "is_nested_target": True,
        "entrypoint_differs_from_internal_target": True,
    }
    assert overlay["dynamic_evidence"]["public_api_evidence"] == (
        overlay["selected_case"]["public_api_evidence"]
    )
    assert overlay["dynamic_evidence"]["overlay_case_context"]["function_name"] == (
        "middle_value.pick_middle"
    )
    assert overlay["selected_case"]["expected_exception"] == "TypeError"
    markdown = render_repository_test_failure_overlay_markdown(overlay)
    assert "Public API Evidence" in markdown
    assert (
        "public_entrypoint_to_nested_target: middle_value(__cia_sequence) -> "
        "middle_value.pick_middle"
    ) in markdown
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["stringified_numeric_value"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_nested_stringified_numeric_helper(
    tmp_path,
):
    _write_method_nested_stringified_numeric_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "stringified_numeric_value"
    assert overlay["selected_case"]["function_name"] == (
        "WindowPicker.middle_value.pick_middle"
    )
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_stringified_numeric_method"
    )
    assert overlay["selected_case"]["call_target"] == "__cia_instance.middle_value"
    assert overlay["selected_case"]["setup_lines"][:1] == [
        "__cia_instance = WindowPicker()"
    ]
    assert overlay["selected_case"]["call_args"] == ["__cia_sequence"]
    assert overlay["selected_case"]["expected_exception"] == "TypeError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["stringified_numeric_value"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_iterator_double_consumption_function(tmp_path):
    _write_iterator_double_consumption_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "iterator_double_consumption"
    assert overlay["selected_case"]["callable_kind"] == "function"
    assert overlay["selected_case"]["expected_exception"] == "ZeroDivisionError"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "average_iterable"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["iterator_double_consumption"] == 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_iterator_double_consumption_method(tmp_path):
    _write_method_iterator_double_consumption_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "iterator_double_consumption"
    assert overlay["selected_case"]["callable_kind"] == "method"
    assert overlay["selected_case"]["function_name"] == "IterableAverager.average_iterable"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "IterableAverager.average_iterable"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_nested_iterator_double_consumption_function(
    tmp_path,
):
    _write_nested_iterator_double_consumption_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "iterator_double_consumption"
    assert overlay["selected_case"]["function_name"] == "average_iterable.average_core"
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_iterator_double_consumption_function"
    )
    assert overlay["selected_case"]["call_target"] == "average_iterable"
    assert overlay["selected_case"]["call_args"] == ["__cia_iterator"]
    assert overlay["selected_case"]["expected_exception"] == "ZeroDivisionError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["iterator_double_consumption"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_nested_iterator_double_consumption(
    tmp_path,
):
    _write_method_nested_iterator_double_consumption_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "iterator_double_consumption"
    assert overlay["selected_case"]["function_name"] == (
        "IterableAverager.average_iterable.average_core"
    )
    assert overlay["selected_case"]["callable_kind"] == (
        "nested_iterator_double_consumption_method"
    )
    assert overlay["selected_case"]["call_target"] == "__cia_instance.average_iterable"
    assert overlay["selected_case"]["setup_lines"][:1] == [
        "__cia_instance = IterableAverager()"
    ]
    assert overlay["selected_case"]["call_args"] == ["__cia_iterator"]
    assert overlay["selected_case"]["expected_exception"] == "ZeroDivisionError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["iterator_double_consumption"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_mutable_default_function(tmp_path):
    _write_mutable_default_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "mutable_default_arg"
    assert overlay["selected_case"]["callable_kind"] == "function"
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "remember"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"] == {"mutable_default_arg": 1}
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_mutable_default_method(tmp_path):
    _write_method_mutable_default_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "mutable_default_arg"
    assert overlay["selected_case"]["callable_kind"] == "method"
    assert overlay["selected_case"]["function_name"] == "Recorder.remember"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "Recorder.remember"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_nested_mutable_default_function(
    tmp_path,
):
    _write_nested_mutable_default_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "mutable_default_arg"
    assert overlay["selected_case"]["function_name"] == "remember.record"
    assert overlay["selected_case"]["callable_kind"] == "nested_mutable_default_function"
    assert overlay["selected_case"]["call_target"] == "remember"
    assert overlay["selected_case"]["call_args"] == ["'__cia_second__'"]
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["mutable_default_arg"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_nested_mutable_default(
    tmp_path,
):
    _write_method_nested_mutable_default_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "mutable_default_arg"
    assert overlay["selected_case"]["function_name"] == "Recorder.remember.record"
    assert overlay["selected_case"]["callable_kind"] == "nested_mutable_default_method"
    assert overlay["selected_case"]["call_target"] == "__cia_instance.remember"
    assert overlay["selected_case"]["setup_lines"][:1] == ["__cia_instance = Recorder()"]
    assert overlay["selected_case"]["call_args"] == ["'__cia_second__'"]
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["mutable_default_arg"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_nested_broad_exception_function(
    tmp_path,
):
    _write_nested_broad_exception_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "broad_exception_pass"
    assert overlay["selected_case"]["function_name"] == "mean_value.mean_core"
    assert (
        overlay["selected_case"]["callable_kind"]
        == "nested_broad_exception_function"
    )
    assert overlay["selected_case"]["call_target"] == "mean_value"
    assert overlay["selected_case"]["call_args"] == ["[]"]
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert overlay["selected_case"]["success_exception"] == "ValueError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["broad_exception_pass"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_method_nested_broad_exception(
    tmp_path,
):
    _write_method_nested_broad_exception_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=5,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "broad_exception_pass"
    assert overlay["selected_case"]["function_name"] == "MeanStats.mean_value.mean_core"
    assert overlay["selected_case"]["callable_kind"] == "nested_broad_exception_method"
    assert overlay["selected_case"]["call_target"] == "__cia_instance.mean_value"
    assert overlay["selected_case"]["setup_lines"][:1] == ["__cia_instance = MeanStats()"]
    assert overlay["selected_case"]["call_args"] == ["[]"]
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert overlay["selected_case"]["success_exception"] == "ValueError"
    assert localization["status"] == "pass"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"]["broad_exception_pass"] >= 1
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_broad_exception_function(tmp_path):
    _write_broad_exception_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "broad_exception_pass"
    assert overlay["selected_case"]["callable_kind"] == "function"
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert overlay["selected_case"]["success_exception"] == "ValueError"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "mean_value"
    assert candidates["status"] == "pass"
    assert candidates["candidate_rule_counts"] == {"broad_exception_pass": 1}
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_statistics_error_broad_exception(
    tmp_path,
):
    _write_statistics_error_broad_exception_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "broad_exception_pass"
    assert overlay["selected_case"]["function_name"] == "mean_value"
    assert overlay["selected_case"]["expected_exception"] == "AssertionError"
    assert overlay["selected_case"]["success_exception"] == "StatisticsError"
    assert "from statistics import StatisticsError" in Path(
        overlay["attempts"][0]["test_path"]
    ).read_text(encoding="utf-8")
    assert localization["status"] == "pass"
    assert localization["top_function"] == "mean_value"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_multi_arg_broad_exception_function(tmp_path):
    _write_multi_arg_broad_exception_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "broad_exception_pass"
    assert overlay["selected_case"]["function_name"] == "mean_value"
    assert overlay["selected_case"]["success_exception"] == "ValueError"
    assert overlay["selected_case"]["call_args"] == ["[]", "'avg'", "False"]
    assert localization["status"] == "pass"
    assert localization["top_function"] == "mean_value"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_broad_exception_method(tmp_path):
    _write_method_broad_exception_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "broad_exception_pass"
    assert overlay["selected_case"]["callable_kind"] == "method"
    assert overlay["selected_case"]["function_name"] == "MeanStats.mean_value"
    assert overlay["selected_case"]["success_exception"] == "ValueError"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "MeanStats.mean_value"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_broad_exception_property(tmp_path):
    _write_property_broad_exception_repo(tmp_path)
    overlay = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        overlay["dynamic_evidence"],
        repository_root=overlay["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=overlay["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=overlay["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert overlay["status"] == "pass"
    assert overlay["selected_case"]["rule_id"] == "broad_exception_pass"
    assert overlay["selected_case"]["callable_kind"] == "property"
    assert overlay["selected_case"]["function_name"] == "MeanStats.mean_value"
    assert overlay["selected_case"]["call_target"] == "__cia_instance.mean_value"
    assert overlay["selected_case"]["call_style"] == "property_access"
    assert overlay["selected_case"]["call_args"] == []
    assert "__cia_instance.values = []" in overlay["selected_case"]["setup_lines"]
    assert overlay["selected_case"]["success_exception"] == "ValueError"
    assert localization["status"] == "pass"
    assert localization["top_function"] == "MeanStats.mean_value"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_classifies_broad_exception_fallback_flow(
    tmp_path,
):
    _write_broad_exception_fallback_flow_repo(tmp_path)
    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )

    counts = payload["strategy_summary"]["candidate_rejection_counts"]
    assert payload["status"] == "skipped"
    assert payload["reason"] == "no_supported_overlay_candidates"
    assert payload["supported_candidate_count"] == 0
    assert counts["broad_exception_fallback_flow_unsupported"] >= 1
    assert "broad_exception_empty_guard_or_raise_unsupported" not in counts
    assert payload["strategy_summary"][
        "dominant_candidate_rejection_reason"
    ] == "broad_exception_fallback_flow_unsupported"
    assert payload["strategy_summary"][
        "dominant_candidate_rejection_count"
    ] >= 1
    assert payload["strategy_summary"]["next_overlay_extension"]["reason"] == (
        "broad_exception_fallback_flow_unsupported"
    )
    assert (
        "audit-only"
        in payload["strategy_summary"]["next_overlay_extension"][
            "recommended_extension"
        ]
    )
    assert payload["strategy_summary"]["candidate_rejection_recommendations"][0][
        "reason"
    ] == "broad_exception_fallback_flow_unsupported"
    assert (
        payload["strategy_summary"]["candidate_rejection_recommendations"][0][
            "actionable"
        ]
        is False
    )
    assert payload["strategy_summary"]["next_actionable_overlay_extension"] == {}
    markdown = render_repository_test_failure_overlay_markdown(payload)
    assert "broad_exception_fallback_flow_unsupported" in markdown
    assert "Next Overlay Extension" in markdown
    assert "Next Actionable Overlay Extension" in markdown


def test_repository_test_failure_overlay_skips_without_supported_findings(tmp_path):
    (tmp_path / "safe.py").write_text(
        "def add_one(value):\n"
        "    return value + 1\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "no_supported_overlay_candidates"
    assert payload["attempted_case_count"] == 0
    assert payload["strategy_summary"]["candidate_rule_counts"] == {}
    assert payload["strategy_summary"]["attempted_rule_counts"] == {}
    assert payload["strategy_summary"]["candidate_rejection_count"] == 0


def test_repository_test_failure_overlay_supports_safe_required_init_method(tmp_path):
    (tmp_path / "sample.py").write_text(
        "class Window:\n"
        "    def __init__(self, size):\n"
        "        self.size = size\n\n"
        "    def shift_left(self, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        payload["dynamic_evidence"],
        repository_root=payload["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=payload["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=payload["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert payload["status"] == "pass"
    assert payload["selected_case"]["callable_kind"] == "method"
    assert payload["selected_case"]["setup_lines"] == ["__cia_instance = Window(0)"]
    assert localization["status"] == "pass"
    assert localization["top_function"] == "Window.shift_left"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_same_file_safe_base_method(tmp_path):
    (tmp_path / "sample.py").write_text(
        "class BaseWindow:\n"
        "    pass\n\n"
        "class Window(BaseWindow):\n"
        "    def shift_left(self, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        payload["dynamic_evidence"],
        repository_root=payload["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=payload["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=payload["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert payload["status"] == "pass"
    assert payload["selected_case"]["callable_kind"] == "method"
    assert payload["selected_case"]["setup_lines"] == ["__cia_instance = Window()"]
    assert localization["status"] == "pass"
    assert localization["top_function"] == "Window.shift_left"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_supports_inherited_safe_base_init_method(tmp_path):
    (tmp_path / "sample.py").write_text(
        "class BaseWindow:\n"
        "    def __init__(self, size: int):\n"
        "        self.size = size\n\n"
        "class Window(BaseWindow):\n"
        "    def shift_left(self, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
        timeout=10,
    )
    localization = build_repository_test_fault_localization(
        payload["dynamic_evidence"],
        repository_root=payload["overlay_root"],
        top_k=3,
    )
    candidates = build_repository_test_patch_candidates(
        localization,
        repository_root=payload["overlay_root"],
        candidate_limit=5,
    )
    validation = build_repository_test_patch_validation(
        candidates,
        repository_root=payload["overlay_root"],
        timeout=10,
        reflection_mode="none",
    )

    assert payload["status"] == "pass"
    assert payload["selected_case"]["callable_kind"] == "method"
    assert payload["selected_case"]["setup_lines"] == ["__cia_instance = Window(0)"]
    assert localization["status"] == "pass"
    assert localization["top_function"] == "Window.shift_left"
    assert candidates["status"] == "pass"
    assert validation["status"] == "pass"
    assert validation["success_count"] >= 1


def test_repository_test_failure_overlay_rejects_unsafe_required_init_method(tmp_path):
    (tmp_path / "sample.py").write_text(
        "def make_size(size):\n"
        "    return size\n\n"
        "class Window:\n"
        "    def __init__(self, size):\n"
        "        self.size = make_size(size)\n\n"
        "    def shift_left(self, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    assert payload["status"] == "skipped"
    assert payload["reason"] == "no_supported_overlay_candidates"
    assert payload["strategy_summary"]["candidate_rejection_counts"][
        "class_init_body_unsupported"
    ] >= 1
    markdown = render_repository_test_failure_overlay_markdown(payload)
    assert "class_init_body_unsupported" in markdown


def test_repository_test_failure_overlay_reports_lifecycle_dunder_rejection(tmp_path):
    (tmp_path / "sample.py").write_text(
        "class Resource:\n"
        "    def __del__(self):\n"
        "        try:\n"
        "            self.close()\n"
        "        except Exception:\n"
        "            pass\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    counts = payload["strategy_summary"]["candidate_rejection_counts"]
    assert payload["status"] == "skipped"
    assert counts["lifecycle_dunder_method_unsupported"] >= 1
    assert "class_base_unsupported_for_instantiation" not in counts
    markdown = render_repository_test_failure_overlay_markdown(payload)
    assert "lifecycle_dunder_method_unsupported" in markdown


def test_repository_test_failure_overlay_reports_returned_factory_callable_arg_rejection(
    tmp_path,
):
    (tmp_path / "sample.py").write_text(
        "from typing import Callable\n\n"
        "def outer(factory: Callable):\n"
        "    def shift_left(values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n"
        "    return shift_left\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    counts = payload["strategy_summary"]["candidate_rejection_counts"]
    assert payload["status"] == "skipped"
    assert counts["returned_nested_factory_callable_arguments_unsupported"] >= 1
    markdown = render_repository_test_failure_overlay_markdown(payload)
    assert "returned_nested_factory_callable_arguments_unsupported" in markdown


def test_repository_test_failure_overlay_reports_returned_nested_no_invocation_args(
    tmp_path,
):
    (tmp_path / "sample.py").write_text(
        "def outer():\n"
        "    cache = {}\n"
        "    def risky():\n"
        "        try:\n"
        "            cache['missing']\n"
        "        except Exception:\n"
        "            pass\n"
        "    return risky\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    counts = payload["strategy_summary"]["candidate_rejection_counts"]
    assert payload["status"] == "skipped"
    assert (
        counts["returned_nested_function_no_invocation_arguments_unsupported"] >= 1
    )
    markdown = render_repository_test_failure_overlay_markdown(payload)
    assert "returned_nested_function_no_invocation_arguments_unsupported" in markdown


def test_repository_test_failure_overlay_classifies_returned_nested_no_arg_fallback_flow(
    tmp_path,
):
    (tmp_path / "sample.py").write_text(
        "def outer():\n"
        "    cache = {}\n"
        "    def risky():\n"
        "        try:\n"
        "            cache['missing']\n"
        "        except Exception:\n"
        "            pass\n"
        "        return None\n"
        "    return risky\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    counts = payload["strategy_summary"]["candidate_rejection_counts"]
    assert payload["status"] == "skipped"
    assert counts["broad_exception_fallback_flow_unsupported"] >= 1
    assert "returned_nested_function_no_invocation_arguments_unsupported" not in counts
    markdown = render_repository_test_failure_overlay_markdown(payload)
    assert "broad_exception_fallback_flow_unsupported" in markdown


def test_repository_test_failure_overlay_reports_specific_callable_rejection_reasons(tmp_path):
    (tmp_path / "sample.py").write_text(
        "from contextlib import contextmanager\n\n"
        "def outer(*seed):\n"
        "    def shift_left(values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n"
        "    return shift_left\n\n"
        "def trace(fn):\n"
        "    def wrapped(*args, **kwargs):\n"
        "        marker = 'not statically transparent'\n"
        "        return fn(*args, **kwargs)\n"
        "    return wrapped\n\n"
        "def safecall(fn):\n"
        "    def wrapper(*args, **kwargs):\n"
        "        try:\n"
        "            return fn(*args, **kwargs)\n"
        "        except Exception:\n"
        "            pass\n"
        "        return None\n"
        "    return wrapper\n\n"
        "class DecoratedWindow:\n"
        "    @trace\n"
        "    def shift_left(self, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n\n"
        "class ReceiverWindow:\n"
        "    def shift_left(cls, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n\n"
        "class BaseWindow:\n"
        "    def __init__(self, token):\n"
        "        self.token = make_token(token)\n\n"
        "class DerivedWindow(BaseWindow):\n"
        "    def shift_left(self, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n\n"
        "class ContextWindow:\n"
        "    @trace\n"
        "    @contextmanager\n"
        "    def shift_left(self, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        yield shifted\n\n"
        "class PropertyWindow:\n"
        "    @property\n"
        "    def risky(self):\n"
        "        try:\n"
        "            self.flush()\n"
        "        except Exception:\n"
        "            pass\n"
        "        return self.values\n",
        encoding="utf-8",
    )

    payload = build_repository_test_failure_overlay(
        repository_root=tmp_path,
        output_dir=tmp_path / "out",
    )

    counts = payload["strategy_summary"]["candidate_rejection_counts"]
    assert payload["status"] == "skipped"
    assert counts["returned_nested_factory_varargs_unsupported"] >= 1
    assert counts["decorator_wrapper_exception_policy_unsupported"] >= 1
    assert counts["method_decorator_unknown_unsupported"] >= 1
    assert counts["contextmanager_method_unsupported"] >= 1
    assert counts["property_method_unsupported"] >= 1
    assert counts["method_receiver_not_self"] >= 1
    assert counts["class_base_unsupported_for_instantiation"] >= 1
    assert payload["strategy_summary"]["next_actionable_overlay_extension"][
        "actionable"
    ] is True
    assert payload["strategy_summary"]["next_actionable_overlay_extension"][
        "reason"
    ] in counts
    markdown = render_repository_test_failure_overlay_markdown(payload)
    assert "returned_nested_factory_varargs_unsupported" in markdown
    assert "decorator_wrapper_exception_policy_unsupported" in markdown
    assert "method_decorator_unknown_unsupported" in markdown
    assert "contextmanager_method_unsupported" in markdown
    assert "property_method_unsupported" in markdown


def _write_index_overrun_repo(root):
    (root / "sample.py").write_text(
        "def shift_left(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values)):\n"
        "        shifted.append(values[i + 1])\n"
        "    return shifted\n",
        encoding="utf-8",
    )


def _write_nested_index_overrun_repo(root):
    (root / "sample.py").write_text(
        "def shift_left(values):\n"
        "    def shift_core(items):\n"
        "        shifted = []\n"
        "        for i in range(len(items)):\n"
        "            shifted.append(items[i + 1])\n"
        "        return shifted\n\n"
        "    return shift_core(values)\n",
        encoding="utf-8",
    )


def _write_identity_literal_repo(root):
    (root / "sample.py").write_text(
        "def is_admin(token):\n"
        "    return token is 'admin'\n",
        encoding="utf-8",
    )


def _write_nested_identity_literal_repo(root):
    (root / "sample.py").write_text(
        "def is_admin_token(token):\n"
        "    def compare_token(candidate):\n"
        "        return candidate is 'admin'\n\n"
        "    return compare_token(token)\n",
        encoding="utf-8",
    )


def _write_method_nested_identity_literal_repo(root):
    (root / "sample.py").write_text(
        "class TokenPolicy:\n"
        "    def is_admin_token(self, token):\n"
        "        def compare_token(candidate):\n"
        "            return candidate is 'admin'\n\n"
        "        return compare_token(token)\n",
        encoding="utf-8",
    )


def _write_enumerate_start_zero_repo(root):
    (root / "sample.py").write_text(
        "def numbered(values):\n"
        "    for position, value in enumerate(values, start=0):\n"
        "        yield position, value\n",
        encoding="utf-8",
    )


def _write_enumerate_average_repo(root):
    (root / "sample.py").write_text(
        "def iterator_average(iterable):\n"
        "    n = 0\n\n"
        "    def count_items():\n"
        "        nonlocal n\n"
        "        for n, value in enumerate(iterable, start=0):\n"
        "            yield value\n\n"
        "    total = sum(count_items())\n"
        "    return total / n\n",
        encoding="utf-8",
    )


def _write_method_enumerate_average_repo(root):
    (root / "sample.py").write_text(
        "class AverageCounter:\n"
        "    def iterator_average(self, iterable):\n"
        "        n = 0\n\n"
        "        def count_items():\n"
        "            nonlocal n\n"
        "            for n, value in enumerate(iterable, start=0):\n"
        "                yield value\n\n"
        "        total = sum(count_items())\n"
        "        return total / n\n",
        encoding="utf-8",
    )


def _write_nested_missing_len_repo(root):
    (root / "sample.py").write_text(
        "def average_value(values):\n"
        "    def average_core(items):\n"
        "        n = len(items)\n"
        "        return sum(items) / n\n\n"
        "    return average_core(values)\n",
        encoding="utf-8",
    )


def _write_method_nested_missing_len_repo(root):
    (root / "sample.py").write_text(
        "class Stats:\n"
        "    def average_value(self, values):\n"
        "        def average_core(items):\n"
        "            n = len(items)\n"
        "            return sum(items) / n\n\n"
        "        return average_core(values)\n",
        encoding="utf-8",
    )


def _write_inverted_empty_guard_repo(root):
    (root / "sample.py").write_text(
        "def mean_value(values):\n"
        "    if values:\n"
        "        raise ValueError('empty input')\n"
        "    return 0\n",
        encoding="utf-8",
    )


def _write_always_true_len_check_repo(root):
    (root / "sample.py").write_text(
        "def require_scheme(scheme):\n"
        "    if len(scheme) >= 0:\n"
        "        return scheme\n"
        "    raise ValueError('missing scheme')\n",
        encoding="utf-8",
    )


def _write_method_always_true_len_check_repo(root):
    (root / "sample.py").write_text(
        "class UrlRules:\n"
        "    def require_scheme(self, scheme):\n"
        "        if 0 <= len(scheme):\n"
        "            return scheme\n"
        "        raise ValueError('missing scheme')\n",
        encoding="utf-8",
    )


def _write_nested_always_true_len_check_repo(root):
    (root / "sample.py").write_text(
        "def require_scheme(scheme):\n"
        "    def validate_scheme(candidate):\n"
        "        if len(candidate) >= 0:\n"
        "            return candidate\n"
        "        raise ValueError('missing scheme')\n\n"
        "    return validate_scheme(scheme)\n",
        encoding="utf-8",
    )


def _write_method_nested_always_true_len_check_repo(root):
    (root / "sample.py").write_text(
        "class UrlRules:\n"
        "    def require_scheme(self, scheme):\n"
        "        def validate_scheme(candidate):\n"
        "            if 0 <= len(candidate):\n"
        "                return candidate\n"
        "            raise ValueError('missing scheme')\n\n"
        "        return validate_scheme(scheme)\n",
        encoding="utf-8",
    )


def _write_nested_inverted_empty_guard_repo(root):
    (root / "sample.py").write_text(
        "def mean_value(values):\n"
        "    def mean_core(items):\n"
        "        if items:\n"
        "            raise ValueError('empty input')\n"
        "        return 0\n\n"
        "    return mean_core(values)\n",
        encoding="utf-8",
    )


def _write_method_nested_inverted_empty_guard_repo(root):
    (root / "sample.py").write_text(
        "class Stats:\n"
        "    def mean_value(self, values):\n"
        "        def mean_core(items):\n"
        "            if items:\n"
        "                raise ValueError('empty input')\n"
        "            return 0\n\n"
        "        return mean_core(values)\n",
        encoding="utf-8",
    )


def _write_nested_dict_missing_key_repo(root):
    (root / "sample.py").write_text(
        "def score_for(scores, name):\n"
        "    def lookup_score(score_map, person):\n"
        "        return score_map[person]\n\n"
        "    return lookup_score(scores, name)\n",
        encoding="utf-8",
    )


def _write_method_nested_dict_missing_key_repo(root):
    (root / "sample.py").write_text(
        "class ScoreBoard:\n"
        "    def score_for(self, scores, name):\n"
        "        def lookup_score(score_map, person):\n"
        "            return score_map[person]\n\n"
        "        return lookup_score(scores, name)\n",
        encoding="utf-8",
    )


def _write_method_index_overrun_repo(root):
    (root / "sample.py").write_text(
        "class Window:\n"
        "    def shift_left(self, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n",
        encoding="utf-8",
    )


def _write_method_nested_index_overrun_repo(root):
    (root / "sample.py").write_text(
        "class Window:\n"
        "    def shift_left(self, values):\n"
        "        def shift_core(items):\n"
        "            shifted = []\n"
        "            for i in range(len(items)):\n"
        "                shifted.append(items[i + 1])\n"
        "            return shifted\n\n"
        "        return shift_core(values)\n",
        encoding="utf-8",
    )


def _write_staticmethod_index_overrun_repo(root):
    (root / "sample.py").write_text(
        "class Window:\n"
        "    @staticmethod\n"
        "    def shift_left(values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n",
        encoding="utf-8",
    )


def _write_classmethod_index_overrun_repo(root):
    (root / "sample.py").write_text(
        "class Window:\n"
        "    @classmethod\n"
        "    def shift_left(cls, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n",
        encoding="utf-8",
    )


def _write_identity_decorated_method_repo(root):
    (root / "sample.py").write_text(
        "def trace(fn):\n"
        "    return fn\n\n"
        "class Window:\n"
        "    @trace\n"
        "    def shift_left(self, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n",
        encoding="utf-8",
    )


def _write_transparent_wrapper_decorated_method_repo(root):
    (root / "sample.py").write_text(
        "def trace(fn):\n"
        "    def wrapped(*args, **kwargs):\n"
        "        return fn(*args, **kwargs)\n"
        "    return wrapped\n\n"
        "class Window:\n"
        "    @trace\n"
        "    def shift_left(self, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n",
        encoding="utf-8",
    )


def _write_returned_nested_function_factory_repo(root):
    (root / "sample.py").write_text(
        "def outer():\n"
        "    def shift_left(values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n"
        "    return shift_left\n",
        encoding="utf-8",
    )


def _write_returned_nested_function_factory_arg_repo(root):
    (root / "sample.py").write_text(
        "def outer(offset: int):\n"
        "    def shift_left(values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        return shifted\n"
        "    return shift_left\n",
        encoding="utf-8",
    )


def _write_returned_nested_broad_exception_repo(root):
    (root / "sample.py").write_text(
        "def outer():\n"
        "    def mean_core(values):\n"
        "        try:\n"
        "            if not values:\n"
        "                raise ValueError('empty input')\n"
        "            return sum(values) / len(values)\n"
        "        except Exception:\n"
        "            pass\n"
        "    return mean_core\n",
        encoding="utf-8",
    )


def _write_contextmanager_index_overrun_repo(root):
    (root / "sample.py").write_text(
        "from contextlib import contextmanager\n\n"
        "class Window:\n"
        "    @contextmanager\n"
        "    def shift_left(self, values):\n"
        "        shifted = []\n"
        "        for i in range(len(values)):\n"
        "            shifted.append(values[i + 1])\n"
        "        yield shifted\n",
        encoding="utf-8",
    )


def _write_contextmanager_function_index_overrun_repo(root):
    (root / "sample.py").write_text(
        "from contextlib import contextmanager\n\n"
        "@contextmanager\n"
        "def shift_left(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values)):\n"
        "        shifted.append(values[i + 1])\n"
        "    yield shifted\n",
        encoding="utf-8",
    )


def _write_rule_diverse_overlay_repo(root):
    (root / "sample.py").write_text(
        "def shift_left(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values)):\n"
        "        shifted.append(values[i + 1])\n"
        "    return shifted\n\n"
        "def shift_right(values):\n"
        "    shifted = []\n"
        "    for i in range(len(values)):\n"
        "        shifted.append(values[i + 1])\n"
        "    return shifted\n\n"
        "def sorted_values(values):\n"
        "    ordered = values.sort()\n"
        "    return ordered\n",
        encoding="utf-8",
    )


def _write_inplace_api_repo(root):
    (root / "sample.py").write_text(
        "def sorted_values(values):\n"
        "    ordered = values.sort()\n"
        "    return ordered\n",
        encoding="utf-8",
    )


def _write_method_inplace_api_repo(root):
    (root / "sample.py").write_text(
        "class Sorter:\n"
        "    def sorted_values(self, values):\n"
        "        ordered = values.sort()\n"
        "        return ordered\n",
        encoding="utf-8",
    )


def _write_nested_inplace_api_repo(root):
    (root / "sample.py").write_text(
        "def sorted_values(values):\n"
        "    def sort_core(items):\n"
        "        ordered = items.sort()\n"
        "        return ordered\n\n"
        "    return sort_core(values)\n",
        encoding="utf-8",
    )


def _write_method_nested_inplace_api_repo(root):
    (root / "sample.py").write_text(
        "class Sorter:\n"
        "    def sorted_values(self, values):\n"
        "        def sort_core(items):\n"
        "            ordered = items.sort()\n"
        "            return ordered\n\n"
        "        return sort_core(values)\n",
        encoding="utf-8",
    )


def _write_stringified_numeric_repo(root):
    (root / "sample.py").write_text(
        "def middle_value(values):\n"
        "    index = str(len(values) // 2)\n"
        "    return values[index]\n",
        encoding="utf-8",
    )


def _write_method_stringified_numeric_repo(root):
    (root / "sample.py").write_text(
        "class WindowPicker:\n"
        "    def middle_value(self, values):\n"
        "        index = str(len(values) // 2)\n"
        "        return values[index]\n",
        encoding="utf-8",
    )


def _write_nested_stringified_numeric_repo(root):
    (root / "sample.py").write_text(
        "def middle_value(values):\n"
        "    def pick_middle(items):\n"
        "        index = str(len(items) // 2)\n"
        "        return items[index]\n\n"
        "    return pick_middle(values)\n",
        encoding="utf-8",
    )


def _write_method_nested_stringified_numeric_repo(root):
    (root / "sample.py").write_text(
        "class WindowPicker:\n"
        "    def middle_value(self, values):\n"
        "        def pick_middle(items):\n"
        "            index = str(len(items) // 2)\n"
        "            return items[index]\n\n"
        "        return pick_middle(values)\n",
        encoding="utf-8",
    )


def _write_iterator_double_consumption_repo(root):
    (root / "sample.py").write_text(
        "def average_iterable(values):\n"
        "    total = sum(values)\n"
        "    count = len(list(values))\n"
        "    return total / count\n",
        encoding="utf-8",
    )


def _write_method_iterator_double_consumption_repo(root):
    (root / "sample.py").write_text(
        "class IterableAverager:\n"
        "    def average_iterable(self, values):\n"
        "        total = sum(values)\n"
        "        count = len(list(values))\n"
        "        return total / count\n",
        encoding="utf-8",
    )


def _write_nested_iterator_double_consumption_repo(root):
    (root / "sample.py").write_text(
        "def average_iterable(values):\n"
        "    def average_core(items):\n"
        "        total = sum(items)\n"
        "        count = len(list(items))\n"
        "        return total / count\n\n"
        "    return average_core(values)\n",
        encoding="utf-8",
    )


def _write_method_nested_iterator_double_consumption_repo(root):
    (root / "sample.py").write_text(
        "class IterableAverager:\n"
        "    def average_iterable(self, values):\n"
        "        def average_core(items):\n"
        "            total = sum(items)\n"
        "            count = len(list(items))\n"
        "            return total / count\n\n"
        "        return average_core(values)\n",
        encoding="utf-8",
    )


def _write_mutable_default_repo(root):
    (root / "sample.py").write_text(
        "def remember(value, cache=[]):\n"
        "    cache.append(value)\n"
        "    return cache\n",
        encoding="utf-8",
    )


def _write_method_mutable_default_repo(root):
    (root / "sample.py").write_text(
        "class Recorder:\n"
        "    def remember(self, value, cache=[]):\n"
        "        cache.append(value)\n"
        "        return cache\n",
        encoding="utf-8",
    )


def _write_nested_mutable_default_repo(root):
    (root / "sample.py").write_text(
        "def remember(value):\n"
        "    def record(item, cache=[]):\n"
        "        cache.append(item)\n"
        "        return cache\n\n"
        "    record('__cia_first__')\n"
        "    return record(value)\n",
        encoding="utf-8",
    )


def _write_method_nested_mutable_default_repo(root):
    (root / "sample.py").write_text(
        "class Recorder:\n"
        "    def remember(self, value):\n"
        "        def record(item, cache=[]):\n"
        "            cache.append(item)\n"
        "            return cache\n\n"
        "        record('__cia_first__')\n"
        "        return record(value)\n",
        encoding="utf-8",
    )


def _write_nested_broad_exception_repo(root):
    (root / "sample.py").write_text(
        "def mean_value(values):\n"
        "    def mean_core(items):\n"
        "        try:\n"
        "            if not items:\n"
        "                raise ValueError('empty input')\n"
        "            return sum(items) / len(items)\n"
        "        except Exception:\n"
        "            pass\n\n"
        "    return mean_core(values)\n",
        encoding="utf-8",
    )


def _write_method_nested_broad_exception_repo(root):
    (root / "sample.py").write_text(
        "class MeanStats:\n"
        "    def mean_value(self, values):\n"
        "        def mean_core(items):\n"
        "            try:\n"
        "                if not items:\n"
        "                    raise ValueError('empty input')\n"
        "                return sum(items) / len(items)\n"
        "            except Exception:\n"
        "                pass\n\n"
        "        return mean_core(values)\n",
        encoding="utf-8",
    )


def _write_broad_exception_repo(root):
    (root / "sample.py").write_text(
        "def mean_value(values):\n"
        "    try:\n"
        "        if not values:\n"
        "            raise ValueError('empty input')\n"
        "        return sum(values) / len(values)\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8",
    )


def _write_statistics_error_broad_exception_repo(root):
    (root / "sample.py").write_text(
        "from statistics import StatisticsError\n\n"
        "def mean_value(values):\n"
        "    try:\n"
        "        if not values:\n"
        "            raise StatisticsError('empty input')\n"
        "        return sum(values) / len(values)\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8",
    )


def _write_multi_arg_broad_exception_repo(root):
    (root / "sample.py").write_text(
        "def mean_value(values, label='avg', strict=False):\n"
        "    try:\n"
        "        if not values:\n"
        "            raise ValueError('empty input')\n"
        "        return f'{label}:{sum(values) / len(values)}'\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8",
    )


def _write_method_broad_exception_repo(root):
    (root / "sample.py").write_text(
        "class MeanStats:\n"
        "    def mean_value(self, values):\n"
        "        try:\n"
        "            if not values:\n"
        "                raise ValueError('empty input')\n"
        "            return sum(values) / len(values)\n"
        "        except Exception:\n"
        "            pass\n",
        encoding="utf-8",
    )


def _write_property_broad_exception_repo(root):
    (root / "sample.py").write_text(
        "class MeanStats:\n"
        "    @property\n"
        "    def mean_value(self):\n"
        "        try:\n"
        "            if not self.values:\n"
        "                raise ValueError('empty input')\n"
        "            return sum(self.values) / len(self.values)\n"
        "        except Exception:\n"
        "            pass\n",
        encoding="utf-8",
    )


def _write_broad_exception_fallback_flow_repo(root):
    (root / "sample.py").write_text(
        "def is_binary_writer(stream, default=False):\n"
        "    try:\n"
        "        stream.write(b'')\n"
        "    except Exception:\n"
        "        try:\n"
        "            stream.write('')\n"
        "            return False\n"
        "        except Exception:\n"
        "            pass\n"
        "        return default\n"
        "    return True\n",
        encoding="utf-8",
    )
