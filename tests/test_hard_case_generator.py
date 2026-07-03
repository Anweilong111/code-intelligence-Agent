import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.ablation import BenchmarkAblationRunner
from code_intelligence_agent.evaluation.benchmark_materializer import (
    BenchmarkMaterializer,
)
from code_intelligence_agent.evaluation.benchmark_recipe_generator import (
    generate_benchmark_recipes,
)
from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkRunner
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator
from code_intelligence_agent.evaluation.hard_case_generator import (
    generate_hard_case_candidates,
    render_hard_case_generation_markdown,
)


def test_hard_case_generator_composes_runnable_multi_patch_case():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload(root)
        suite_payload = _suite_payload(
            bucket_counts={"easy": 8, "medium": 2, "hard": 5},
            label_counts={
                "multi_ground_truth": 2,
                "multi_patch_bundle": 0,
                "wide_beam_search": 2,
                "failed_before_success": 2,
                "reflection_depth": 2,
                "cross_file_patch": 3,
                "patch_candidate_competition": 8,
                "cross_function_trace": 8,
            },
        )

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        template = root / "generated_hard_case_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        case_result = benchmark_report.cases[0]

        assert report.generated_count == 1
        assert report.rows[0].target_signal == "multi_patch_bundle"
        assert report.rows[0].strategy == "multi_bug_composition"
        assert template_case["benchmark"]["metadata"]["hard_case_generated"] is True
        assert template_case["benchmark"]["metadata"]["hard_case_target_signal"] == (
            "multi_patch_bundle"
        )
        assert template_case["benchmark"]["metadata"]["bugs_per_case"] == 2
        assert validation.is_valid
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.multi_patch_success_rate == 1.0
        assert case_result.multi_patch_bundle_size == 2


def test_hard_case_generator_prioritizes_high_signal_candidates_over_catalog_order():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload_with_decoys_first(root)
        suite_payload = _suite_payload(
            bucket_counts={"easy": 8, "medium": 2, "hard": 5},
            label_counts={
                "multi_ground_truth": 2,
                "multi_patch_bundle": 0,
                "wide_beam_search": 2,
                "failed_before_success": 2,
                "reflection_depth": 2,
                "cross_file_patch": 3,
                "patch_candidate_competition": 8,
                "cross_function_trace": 8,
            },
        )

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        selected = set(row["selected_candidate_ids"])
        selected_rules = {
            rule
            for candidate in catalog_payload["candidates"]
            if candidate["id"] in selected
            for rule in candidate["rule_ids"]
        }
        case_metadata = payload["template"]["cases"][0]["benchmark"]["metadata"]

        assert row["selection_policy"] == "risk_weighted_multi_bug_candidate_priority"
        assert selected_rules == {
            "missing_len_zero_guard",
            "possible_index_overrun",
        }
        assert row["selected_candidate_scores"]
        assert row["selection_reasons"]
        assert any("selected_candidate=" in reason for reason in row["reasons"])
        assert case_metadata["hard_case_selection_policy"] == row["selection_policy"]
        assert set(case_metadata["hard_case_selected_candidate_ids"]) == selected
        assert case_metadata["hard_case_selected_candidate_scores"]
        assert case_metadata["hard_case_selected_candidate_reasons"]


def test_hard_case_generator_prefers_provenance_rich_candidate_on_signal_tie():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        base_catalog = _catalog_payload_with_two_index_candidates(root)
        index_candidates = [
            candidate
            for candidate in base_catalog["candidates"]
            if candidate["rule_ids"] == ["possible_index_overrun"]
        ]
        catalog_payload = {
            "candidates": [
                _rename_candidate(index_candidates[0], "a_low_provenance_index"),
                _with_complete_provenance(
                    _rename_candidate(
                        index_candidates[1],
                        "z_provenance_rich_index",
                    ),
                ),
            ]
        }

        report = generate_hard_case_candidates(
            _search_competition_suite_payload(),
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        selected = row["selected_candidate_ids"]
        reasons = row["selection_reasons"]["z_provenance_rich_index"]

        assert row["target_signal"] == "search_score_inversion"
        assert selected == ["z_provenance_rich_index"]
        assert any(reason.startswith("provenance_bonus=") for reason in reasons)
        assert any("stable_ref=1.0000" in reason for reason in reasons)
        assert any("source_sha256=1.0000" in reason for reason in reasons)


def test_hard_case_generator_does_not_select_provenance_only_rule_mismatch():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        base_catalog = _catalog_payload(root)
        missing_len = next(
            candidate
            for candidate in base_catalog["candidates"]
            if candidate["rule_ids"] == ["missing_len_zero_guard"]
        )
        index = next(
            candidate
            for candidate in base_catalog["candidates"]
            if candidate["rule_ids"] == ["possible_index_overrun"]
        )
        catalog_payload = {
            "candidates": [
                _with_complete_provenance(
                    _rename_candidate(
                        missing_len,
                        "a_provenance_only_missing_len",
                    ),
                ),
                _rename_candidate(index, "z_signal_matched_index"),
            ]
        }

        report = generate_hard_case_candidates(
            _search_candidate_competition_only_suite_payload(),
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        selected = row["selected_candidate_ids"]
        selected_reasons = [
            reason
            for reasons in row["selection_reasons"].values()
            for reason in reasons
        ]

        assert row["target_signal"] == "search_candidate_competition"
        assert selected == ["z_signal_matched_index"]
        assert "a_provenance_only_missing_len" not in selected
        assert not any(
            reason.startswith("provenance_bonus=")
            for reason in selected_reasons
        )


def test_hard_case_generator_uses_diversity_when_generating_multiple_cases():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload_with_decoys_first(root)
        suite_payload = _suite_payload(
            bucket_counts={"easy": 8, "medium": 2, "hard": 5},
            label_counts={
                "multi_ground_truth": 2,
                "multi_patch_bundle": 0,
                "wide_beam_search": 2,
                "failed_before_success": 2,
                "reflection_depth": 2,
                "cross_file_patch": 3,
                "patch_candidate_competition": 8,
                "cross_function_trace": 8,
            },
        )

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_cases_per_suggestion=2,
            max_total_cases=2,
        )
        payload = report.to_dict()
        case_rule_sets = [
            set(case["benchmark"]["metadata"]["composed_rules"])
            for case in payload["template"]["cases"]
        ]
        selected_ids = payload["rows"][0]["selected_candidate_ids"]
        selection_reasons = payload["rows"][0]["selection_reasons"]
        audit = payload["selection_audit"]

        assert report.generated_count == 2
        assert {"missing_len_zero_guard", "possible_index_overrun"} in case_rule_sets
        assert {"inplace_api_return_value", "mutable_default_arg"} in case_rule_sets
        assert len(selected_ids) == 4
        assert any(
            any("new_rule=" in reason for reason in reasons)
            for reasons in selection_reasons.values()
        )
        assert audit["selected_candidate_count"] == 4
        assert audit["selected_rule_count"] == 4
        assert set(audit["selected_rules"]) == {
            "inplace_api_return_value",
            "missing_len_zero_guard",
            "mutable_default_arg",
            "possible_index_overrun",
        }
        assert audit["selected_function_count"] == 4
        assert audit["selected_source_count"] == 4
        assert audit["average_candidate_score"] > 0
        assert audit["average_diversity_bonus"] > 0
        assert audit["selection_policies"] == [
            "risk_weighted_multi_bug_candidate_priority"
        ]


def test_hard_case_generator_supports_ablation_regression_signals():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload_with_two_index_candidates(root)
        suite_payload = _ablation_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=2,
        )
        payload = report.to_dict()
        rows = {row["target_signal"]: row for row in payload["rows"]}
        template = root / "ablation_generated_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        static_case = next(
            case
            for case in payload["template"]["cases"]
            if case["benchmark"]["metadata"]["hard_case_target_signal"]
            == "without_static_rules"
        )
        static_metadata = static_case["benchmark"]["metadata"]
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_ablation",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        static_result = next(
            case
            for case in benchmark_report.cases
            if case.case_name == static_case["name"]
        )
        search_competition = benchmark_report.to_dict()["summary"][
            "search_competition_analysis"
        ]

        assert report.generated_count == 2
        assert rows["without_static_rules"]["generated_count"] == 1
        assert rows["without_static_rules"]["strategy"] == (
            "ablation_static_rule_catalog_selection"
        )
        assert rows["without_static_rules"]["selection_policy"] == (
            "ablation_static_rule_candidate_priority"
        )
        assert rows["without_beam_search"]["generated_count"] == 1
        assert rows["without_beam_search"]["strategy"] == (
            "ablation_beam_search_multi_bug_composition"
        )
        assert rows["without_beam_search"]["include_rules"] == [
            "possible_index_overrun"
        ]
        assert validation.is_valid
        assert {
            case["benchmark"]["metadata"]["hard_case_target_signal"]
            for case in payload["template"]["cases"]
        } == {"without_static_rules", "without_beam_search"}
        assert static_metadata["patch_score_profile"] == "prior_decoy_score_inversion"
        assert static_metadata["search_score_inversion_profile"] == (
            "prior_decoy_score_inversion"
        )
        assert static_metadata["score_inversion_decoy_variant"] == (
            "return_default_on_empty"
        )
        assert static_metadata["score_inversion_success_variant"] == (
            "insert_len_zero_guard"
        )
        assert static_metadata["expected_score_inversion"] is True
        assert "search_score_inversion" in static_metadata[
            "hard_case_target_signals"
        ]
        assert "search_score_inversion" in static_metadata[
            "target_benchmark_signals"
        ]
        assert benchmark_report.patch_success_rate == 1.0
        assert static_result.search_analysis["first_success_rank"] == 2
        assert static_result.beam_search_results[0]["variant"] == (
            "return_default_on_empty"
        )
        assert static_result.beam_search_results[0]["success"] is False
        assert static_result.beam_search_results[1]["success"] is True
        assert search_competition["score_inversion_count"] >= 1
        assert payload["selection_audit"]["selected_candidate_count"] >= 2
        assert "without_static_rules" in payload["selection_audit"]["target_signals"]
        assert "without_beam_search" in payload["selection_audit"]["target_signals"]


def test_hard_case_generator_supports_direct_search_repair_ablation_regressions():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload_with_single_index_search_decoys(root)
        suite_payload = _direct_search_repair_ablation_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=4,
        )
        payload = report.to_dict()
        rows = {row["target_signal"]: row for row in payload["rows"]}
        cases = {
            case["benchmark"]["metadata"]["hard_case_target_signal"]: case
            for case in payload["template"]["cases"]
        }
        template = root / "direct_search_repair_ablation_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_direct_search_repair_ablation",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        search_budget = benchmark_report.to_dict()["summary"]["search_budget_analysis"]

        assert report.generated_count == 4
        assert rows["without_reflection"]["strategy"] == (
            "ablation_reflection_depth_probe"
        )
        assert rows["without_patch_prior"]["strategy"] == (
            "ablation_patch_prior_score_inversion_probe"
        )
        assert rows["without_diversity_reranking"]["strategy"] == (
            "ablation_diversity_reranking_probe"
        )
        assert rows["without_candidate_deduplication"]["strategy"] == (
            "ablation_candidate_deduplication_probe"
        )
        assert cases["without_reflection"]["benchmark"]["metadata"][
            "expected_reflection_depth"
        ] is True
        assert cases["without_patch_prior"]["benchmark"]["metadata"][
            "expected_score_inversion"
        ] is True
        assert cases["without_diversity_reranking"]["benchmark"]["metadata"][
            "expected_diversity_reranking"
        ] is True
        assert cases["without_candidate_deduplication"]["benchmark"]["metadata"][
            "expected_candidate_deduplication"
        ] is True
        assert "reflection_depth" in cases["without_reflection"]["benchmark"][
            "metadata"
        ]["target_benchmark_signals"]
        assert "search_score_inversion" in cases["without_patch_prior"][
            "benchmark"
        ]["metadata"]["target_benchmark_signals"]
        assert "search_diversity_reranking" in cases[
            "without_diversity_reranking"
        ]["benchmark"]["metadata"]["target_benchmark_signals"]
        assert "candidate_deduplication_pressure" in cases[
            "without_candidate_deduplication"
        ]["benchmark"]["metadata"]["target_benchmark_signals"]
        assert validation.is_valid
        assert len(benchmark_report.cases) == 4
        assert benchmark_report.patch_success_rate == 1.0
        assert search_budget["dedupe_affected_case_count"] >= 1
        assert search_budget["total_deduplicated_candidates"] >= 3
        assert search_budget["average_duplicate_pressure"] > 0.0


def test_hard_case_generator_uses_dict_rule_for_static_rule_gap():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload_with_dict_candidate(root)
        suite_payload = _static_rule_gap_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template_case = payload["template"]["cases"][0]
        selected_reasons = [
            reason
            for reasons in row["selection_reasons"].values()
            for reason in reasons
        ]
        template = root / "dict_static_rule_gap_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)

        assert report.generated_count == 1
        assert row["target_signal"] == "without_static_rules"
        assert row["selection_policy"] == "ablation_static_rule_candidate_priority"
        assert "dict_missing_key_guard" in row["include_rules"]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "dict_missing_key_guard"
        ]
        assert "dict_missing_key_guard" in payload["selection_audit"][
            "selected_rules"
        ]
        assert "key error" in payload["selection_audit"]["selected_bug_types"]
        assert (
            "without_static_rules_rule_weight=dict_missing_key_guard:7.0"
            in selected_reasons
        )
        assert "bug_type=key error:2.5" in selected_reasons
        assert validation.is_valid


def test_hard_case_generator_supports_data_dependency_ablation_regression():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload(root)
        suite_payload = _data_dependency_ablation_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template_case = payload["template"]["cases"][0]
        metadata = template_case["benchmark"]["metadata"]
        selected_reasons = [
            reason
            for reasons in row["selection_reasons"].values()
            for reason in reasons
        ]
        template = root / "data_dependency_ablation_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_data_dependency_ablation",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        detail = benchmark_report.cases[0].localization_details[0]

        assert report.generated_count == 1
        assert row["target_signal"] == "without_data_dependency"
        assert row["strategy"] == "ablation_data_dependency_cross_file_composition"
        assert row["selection_policy"] == "ablation_data_dependency_candidate_priority"
        assert row["wrapper_depth"] == 1
        assert metadata["hard_case_generation_source"] == "ablation_regression"
        assert metadata["hard_case_target_signal"] == "without_data_dependency"
        assert "without_data_dependency" in metadata["hard_case_target_signals"]
        assert metadata["cross_file_trace"] is True
        assert (
            "without_data_dependency_rule_weight=missing_len_zero_guard:10.0"
            in selected_reasons
        )
        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert detail["data_flow_evidence"]["cross_function_edges"] > 0


def test_hard_case_generator_supports_multi_patch_repair_ablation_regression():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload(root)
        suite_payload = _multi_patch_ablation_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template_case = payload["template"]["cases"][0]
        metadata = template_case["benchmark"]["metadata"]
        selected_reasons = [
            reason
            for reasons in row["selection_reasons"].values()
            for reason in reasons
        ]
        template = root / "multi_patch_ablation_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_multi_patch_ablation",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        case_result = benchmark_report.cases[0]
        difficulty = benchmark_report.to_dict()["summary"]["difficulty_report"][
            "cases"
        ][0]

        assert report.generated_count == 1
        assert row["target_signal"] == "without_multi_patch_repair"
        assert row["strategy"] == "ablation_multi_patch_composition"
        assert row["selection_policy"] == "ablation_multi_patch_candidate_priority"
        assert metadata["hard_case_generation_source"] == "ablation_regression"
        assert metadata["hard_case_target_signal"] == "without_multi_patch_repair"
        assert "without_multi_patch_repair" in metadata["hard_case_target_signals"]
        assert (
            "without_multi_patch_repair_rule_weight=possible_index_overrun:9.0"
            in selected_reasons
            or "without_multi_patch_repair_rule_weight=missing_len_zero_guard:8.0"
            in selected_reasons
        )
        assert validation.is_valid
        assert case_result.patch_success is True
        assert case_result.multi_patch_success is True
        assert case_result.multi_patch_bundle_size == 2
        assert "multi_patch_bundle" in difficulty["labels"]


def test_hard_case_generator_supports_graph_bundle_search_ablation_regression():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        suite_payload = _graph_bundle_ablation_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            {"candidates": []},
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template_case = payload["template"]["cases"][0]
        metadata = template_case["benchmark"]["metadata"]
        template = root / "graph_bundle_ablation_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_graph_bundle_ablation",
        )
        ablation = {
            item.variant: item
            for item in BenchmarkAblationRunner(
                use_dynamic_coverage=False
            ).run_manifest(manifest)
        }

        assert report.generated_count == 1
        assert row["target_signal"] == "without_graph_bundle_search"
        assert row["strategy"] == "ablation_graph_bundle_pressure_synthetic"
        assert row["selection_policy"] == "synthetic_graph_bundle_pressure"
        assert row["include_rules"] == ["dict_missing_key_guard"]
        assert metadata["hard_case_generation_source"] == "ablation_regression"
        assert metadata["hard_case_target_signal"] == "without_graph_bundle_search"
        assert "without_graph_bundle_search" in metadata["hard_case_target_signals"]
        assert metadata["expected_graph_bundle_pressure"] is True
        assert metadata["expected_repair_bundle_functions"] == ["z_left", "z_right"]
        assert validation.is_valid
        assert ablation["full"].patch_success_rate == 1.0
        assert ablation["full"].multi_patch_success_rate == 1.0
        assert ablation["without_graph_bundle_search"].patch_success_rate == 0.0
        assert (
            ablation["without_graph_bundle_search"].multi_patch_success_rate == 0.0
        )


def test_hard_case_generator_supports_rule_precision_filter_ablation_regression():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        suite_payload = _rule_precision_filter_ablation_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            {"candidates": []},
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template_case = payload["template"]["cases"][0]
        metadata = template_case["benchmark"]["metadata"]
        template = root / "rule_precision_filter_ablation_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_rule_precision_filter_ablation",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        ablation = {
            item.variant: item
            for item in BenchmarkAblationRunner(
                use_dynamic_coverage=False
            ).run_manifest(manifest)
        }

        assert report.generated_count == 1
        assert row["target_signal"] == "without_rule_precision_filter"
        assert row["strategy"] == (
            "ablation_rule_precision_filter_pressure_synthetic"
        )
        assert row["selection_policy"] == "synthetic_rule_precision_filter_pressure"
        assert row["include_rules"] == [
            "possible_index_overrun",
            "missing_len_zero_guard",
            "stringified_numeric_value",
            "inplace_api_return_value",
        ]
        assert metadata["hard_case_generation_source"] == "ablation_regression"
        assert metadata["hard_case_target_signal"] == "without_rule_precision_filter"
        assert "without_rule_precision_filter" in metadata["hard_case_target_signals"]
        assert metadata["expected_rule_precision_pressure"] is True
        assert metadata["expected_filtered_false_positive_rules"] == [
            "missing_len_zero_guard",
            "stringified_numeric_value",
            "inplace_api_return_value",
        ]
        assert validation.is_valid
        assert benchmark_report.patch_success_rate == 1.0
        assert ablation["full"].expected_rule_precision == 1.0
        assert ablation["without_rule_precision_filter"].expected_rule_precision < 1.0
        assert ablation["without_rule_precision_filter"].average_extra_rules >= 3.0


def test_hard_case_generator_uses_dict_rule_for_subscript_key_flow_gap():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload_with_dict_candidate(root)
        suite_payload = _subscript_key_flow_gap_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template_case = payload["template"]["cases"][0]
        metadata = template_case["benchmark"]["metadata"]
        selected_reasons = [
            reason
            for reasons in row["selection_reasons"].values()
            for reason in reasons
        ]
        template = root / "subscript_key_flow_gap_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_subscript_key_flow",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        summary = benchmark_report.to_dict()["summary"]

        assert report.generated_count == 1
        assert row["target_signal"] == "subscript_key_flow"
        assert row["strategy"] == "subscript_key_flow_catalog_selection"
        assert row["selection_policy"] == "subscript_key_flow_candidate_priority"
        assert row["include_rules"] == ["dict_missing_key_guard"]
        assert template_case["benchmark"]["expected_rule_ids"] == [
            "dict_missing_key_guard"
        ]
        assert metadata["hard_case_generation_source"] == "data_flow_evidence_gap"
        assert metadata["hard_case_target_signal"] == "subscript_key_flow"
        assert "subscript_key_flow" in metadata["hard_case_target_signals"]
        assert (
            "subscript_key_flow_rule_weight=dict_missing_key_guard:16.0"
            in selected_reasons
        )
        assert "bug_type=key error:5.0" in selected_reasons
        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert summary["subscript_key_flow_case_count"] == 1
        assert (
            benchmark_report.cases[0].localization_details[0]["data_flow_evidence"][
                "key_flow_edges"
            ]
            > 0
        )


def test_hard_case_generator_uses_cross_file_composition_for_cross_function_data_flow_gap():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload(root)
        suite_payload = _cross_function_data_flow_gap_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template_case = payload["template"]["cases"][0]
        metadata = template_case["benchmark"]["metadata"]
        template = root / "cross_function_data_flow_gap_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_cross_function_data_flow",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        summary = benchmark_report.to_dict()["summary"]
        detail = benchmark_report.cases[0].localization_details[0]
        difficulty = summary["difficulty_report"]["cases"][0]

        assert report.generated_count == 1
        assert row["target_signal"] == "cross_function_data_flow"
        assert row["strategy"] == "cross_file_composition"
        assert row["selection_policy"] == "risk_weighted_cross_file_candidate_priority"
        assert row["wrapper_depth"] == 1
        assert metadata["hard_case_generation_source"] == "data_flow_evidence_gap"
        assert metadata["hard_case_target_signal"] == "cross_function_data_flow"
        assert "cross_function_data_flow" in metadata["hard_case_target_signals"]
        assert metadata["cross_file_trace"] is True
        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert summary["cross_function_data_flow_case_count"] == 1
        assert detail["data_flow_evidence"]["cross_function_edges"] > 0
        assert "cross_function_data_flow" in difficulty["labels"]


def test_hard_case_generator_uses_search_competition_gap_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload_with_two_index_candidates(root)
        suite_payload = _search_competition_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template = root / "search_competition_generated_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        metadata = payload["template"]["cases"][0]["benchmark"]["metadata"]
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_search_competition",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        search_competition = benchmark_report.to_dict()["summary"][
            "search_competition_analysis"
        ]
        case_result = benchmark_report.cases[0]

        assert report.generated_count == 1
        assert row["target_signal"] == "search_score_inversion"
        assert row["strategy"] == "search_competition_score_inversion_probe"
        assert row["selection_policy"] == "search_competition_candidate_priority"
        assert "possible_index_overrun" in row["include_rules"]
        assert "broad_exception_pass" in row["include_rules"]
        assert metadata["patch_score_profile"] == "prior_decoy_score_inversion"
        assert metadata["expected_score_inversion"] is True
        assert metadata["hard_case_generation_source"] == "search_competition_gap"
        assert metadata["hard_case_target_signal"] == "search_score_inversion"
        assert validation.is_valid
        assert benchmark_report.patch_success_rate == 1.0
        assert case_result.search_analysis["first_success_rank"] == 2
        assert case_result.beam_search_results[0]["variant"] == (
            "overly_conservative_range_bound"
        )
        assert case_result.beam_search_results[0]["success"] is False
        assert case_result.beam_search_results[1]["success"] is True
        assert search_competition["score_inversion_count"] == 1
        assert "search_score_inversion" in payload["selection_audit"]["target_signals"]


def test_hard_case_generator_uses_search_diversity_reranking_gap_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload_with_two_index_candidates(root)
        suite_payload = _search_diversity_reranking_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template = root / "search_diversity_generated_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        metadata = payload["template"]["cases"][0]["benchmark"]["metadata"]
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_search_diversity",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        search_competition = benchmark_report.to_dict()["summary"][
            "search_competition_analysis"
        ]
        case_result = benchmark_report.cases[0]
        success_node = next(
            node for node in case_result.beam_search_results if node["success"]
        )
        success_diversity = success_node["search_diversity"]

        assert report.generated_count == 1
        assert row["target_signal"] == "search_diversity_reranking"
        assert row["strategy"] == "search_competition_diversity_reranking_probe"
        assert row["selection_policy"] == "search_competition_candidate_priority"
        assert metadata["patch_score_profile"] == "diversity_reranking_probe"
        assert metadata["expected_diversity_reranking"] is True
        assert metadata["expected_diversity_assisted_success"] is True
        assert metadata["hard_case_generation_source"] == "search_competition_gap"
        assert metadata["hard_case_target_signal"] == "search_diversity_reranking"
        assert "search_diversity_reranking" in metadata[
            "hard_case_target_signals"
        ]
        assert validation.is_valid
        assert benchmark_report.patch_success_rate == 1.0
        assert search_competition["diversity_assisted_success_count"] == 1
        assert search_competition["average_success_diversity_lift"] >= 1.0
        assert search_competition["average_success_diversity_bonus"] > 0.0
        assert success_diversity["base_rank"] > success_diversity["rank"]
        assert "new_rule" in success_diversity["reasons"]
        assert any(
            node["search_profile_role"] == "diversity_duplicate_decoy"
            and not node["success"]
            for node in case_result.beam_search_results
        )
        assert payload["selection_audit"]["target_signals"] == [
            "search_diversity_reranking"
        ]


def test_hard_case_generator_uses_candidate_deduplication_budget_gap_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload_with_single_index_search_decoys(root)
        suite_payload = _candidate_deduplication_budget_gap_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template = root / "candidate_deduplication_generated_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        metadata = payload["template"]["cases"][0]["benchmark"]["metadata"]
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_candidate_deduplication",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        search_budget = benchmark_report.to_dict()["summary"]["search_budget_analysis"]
        case_result = benchmark_report.cases[0]

        assert report.generated_count == 1
        assert row["target_signal"] == "candidate_deduplication_pressure"
        assert row["strategy"] == "search_budget_candidate_deduplication_probe"
        assert row["selection_policy"] == "search_competition_candidate_priority"
        assert metadata["patch_score_profile"] == "candidate_deduplication_probe"
        assert metadata["expected_candidate_deduplication"] is True
        assert metadata["hard_case_generation_source"] == "search_budget_gap"
        assert metadata["hard_case_target_signal"] == "candidate_deduplication_pressure"
        assert "candidate_deduplication_pressure" in metadata[
            "hard_case_target_signals"
        ]
        assert validation.is_valid
        assert benchmark_report.patch_success_rate == 1.0
        assert search_budget["dedupe_affected_case_count"] == 1
        assert search_budget["total_deduplicated_candidates"] >= 3
        assert search_budget["average_duplicate_pressure"] > 0.0
        assert case_result.search_analysis["deduplicated_candidates"] >= 3
        assert any(
            node["search_profile_role"] == "candidate_deduplication_duplicate"
            and node["search_duplicate_count"] >= 3
            and not node["success"]
            for node in case_result.beam_search_results
        )
        assert any(
            node["search_profile_role"] == "candidate_deduplication_success"
            and node["success"]
            for node in case_result.beam_search_results
        )
        assert payload["selection_audit"]["target_signals"] == [
            "candidate_deduplication_pressure"
        ]


def test_hard_case_generator_emits_reflection_depth_probe_case():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload(root)

        report = generate_hard_case_candidates(
            _reflection_gap_suite_payload(),
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        metadata = template_case["benchmark"]["metadata"]
        template = root / "reflection_depth_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_reflection_depth",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        case_result = benchmark_report.cases[0]
        reflection = benchmark_report.to_dict()["summary"]["reflection_analysis"]

        assert report.generated_count == 1
        assert report.rows[0].target_signal == "reflection_depth"
        assert report.rows[0].strategy == "reflection_depth_probe"
        assert metadata["patch_score_profile"] == "reflection_depth_probe"
        assert metadata["expected_reflection_depth"] is True
        assert metadata["hard_case_generation_strategy"] == "reflection_depth_probe"
        assert validation.is_valid
        assert case_result.patch_success is True
        assert case_result.repair_strategy == "beam_search"
        assert case_result.repair_rounds == 2
        assert any(
            node["depth"] == 1 and node["success"]
            for node in case_result.beam_search_results
        )
        assert reflection["reflection_success_case_count"] == 1
        assert reflection["reflection_candidate_count"] >= 1


def test_hard_case_generator_falls_back_to_synthetic_reflection_depth_probe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)

        report = generate_hard_case_candidates(
            _reflection_gap_suite_payload(),
            {"candidates": []},
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template_case = payload["template"]["cases"][0]
        metadata = template_case["benchmark"]["metadata"]
        template = root / "synthetic_reflection_depth_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_synthetic_reflection_depth",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        case_result = benchmark_report.cases[0]
        reflection = benchmark_report.to_dict()["summary"]["reflection_analysis"]

        assert report.generated_count == 1
        assert row["target_signal"] == "reflection_depth"
        assert row["strategy"] == "reflection_depth_probe_synthetic"
        assert row["selection_policy"] == "synthetic_reflection_depth_pressure"
        assert row["selected_candidate_ids"] == [
            "synthetic_reflection_depth_pressure"
        ]
        assert row["include_rules"] == [
            "possible_index_overrun",
            "missing_len_zero_guard",
        ]
        assert metadata["source"] == "synthetic_reflection_depth_pressure"
        assert metadata["patch_score_profile"] == "reflection_depth_probe"
        assert metadata["expected_reflection_depth"] is True
        assert metadata["hard_case_generation_strategy"] == (
            "reflection_depth_probe_synthetic"
        )
        assert "reflection_depth" in metadata["target_benchmark_signals"]
        assert validation.is_valid
        assert benchmark_report.patch_success_rate == 1.0
        assert case_result.patch_success is True
        assert case_result.repair_strategy == "beam_search"
        assert case_result.repair_rounds == 2
        assert case_result.search_analysis["first_success_depth"] == 1
        assert any(
            node["depth"] == 1
            and node["success"]
            and node["variant"] == "reflection_shrink_range_upper_bound"
            for node in case_result.beam_search_results
        )
        assert reflection["reflection_success_case_count"] == 1
        assert reflection["successful_reflection_candidate_count"] == 1
        assert reflection["reflection_candidate_count"] >= 1
        assert reflection["average_success_reflection_depth"] == 1.0


def test_hard_case_generator_falls_back_to_synthetic_reflection_ablation_probe():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)

        report = generate_hard_case_candidates(
            _reflection_ablation_suite_payload(),
            {"candidates": []},
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template_case = payload["template"]["cases"][0]
        metadata = template_case["benchmark"]["metadata"]
        template = root / "synthetic_reflection_ablation_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_synthetic_reflection_ablation",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        reflection = benchmark_report.to_dict()["summary"]["reflection_analysis"]

        assert report.generated_count == 1
        assert row["target_signal"] == "without_reflection"
        assert row["strategy"] == "ablation_reflection_depth_probe_synthetic"
        assert row["selection_policy"] == "synthetic_reflection_depth_pressure"
        assert metadata["hard_case_generation_source"] == "ablation_regression"
        assert metadata["hard_case_target_signal"] == "without_reflection"
        assert metadata["expected_reflection_depth"] is True
        assert "reflection_depth" in metadata["target_benchmark_signals"]
        assert validation.is_valid
        assert benchmark_report.patch_success_rate == 1.0
        assert reflection["reflection_success_case_count"] == 1
        assert reflection["successful_reflection_candidate_count"] == 1


def test_hard_case_generator_diversifies_search_competition_gap_signals():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload_with_single_index_search_decoys(root)
        suite_payload = _search_competition_full_gap_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=3,
        )
        payload = report.to_dict()
        rows = {row["target_signal"]: row for row in payload["rows"]}
        template = root / "search_competition_generated_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        case_names = [case["name"] for case in payload["template"]["cases"]]

        assert report.generated_count == 3
        assert rows["search_candidate_competition"]["status"] == "generated"
        assert rows["search_score_inversion"]["status"] == "generated"
        assert rows["search_diversity_reranking"]["status"] == "generated"
        assert rows["search_candidate_competition"]["strategy"] == (
            "search_competition_multi_bug_composition"
        )
        assert rows["search_score_inversion"]["strategy"] == (
            "search_competition_score_inversion_probe"
        )
        assert rows["search_diversity_reranking"]["strategy"] == (
            "search_competition_diversity_reranking_probe"
        )
        assert "inplace_api_return_value" in rows[
            "search_candidate_competition"
        ]["include_rules"]
        assert "broad_exception_pass" in rows["search_score_inversion"][
            "include_rules"
        ]
        assert "possible_index_overrun" in rows["search_diversity_reranking"][
            "include_rules"
        ]
        assert len(set(case_names)) == 3
        assert validation.is_valid
        generated_signals = {
            row["target_signal"]
            for row in payload["rows"]
            if row["status"] == "generated"
        }
        assert generated_signals == {
            "search_candidate_competition",
            "search_diversity_reranking",
            "search_score_inversion",
        }
        assert "search_failure_pressure" in payload["selection_audit"][
            "target_signals"
        ]


def test_hard_case_generator_uses_slice_grounding_gap_signal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload(root)
        suite_payload = _slice_grounding_gap_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template_case = payload["template"]["cases"][0]
        metadata = template_case["benchmark"]["metadata"]
        template = root / "slice_grounding_generated_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_slice_grounding",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        details = benchmark_report.cases[0].localization_details[0]

        assert report.generated_count == 1
        assert row["target_signal"] == "weak_slice_grounding"
        assert row["strategy"] == "slice_grounding_cross_file_composition"
        assert row["selection_policy"] == "slice_grounding_candidate_priority"
        assert row["wrapper_depth"] == 2
        assert "missing_len_zero_guard" in row["include_rules"]
        assert metadata["hard_case_generation_source"] == "slice_grounding_gap"
        assert metadata["hard_case_target_signal"] == "weak_slice_grounding"
        assert metadata["wrapper_depth"] == 2
        assert "weak_slice_grounding" in metadata["hard_case_target_signals"]
        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert details["call_chain_length"] == 3
        assert details["slice_grounding"]["grounded"] is True
        assert payload["selection_audit"]["target_signals"] == [
            "weak_slice_grounding"
        ]


def test_hard_case_generator_prefers_slice_rule_diversity_for_slice_gap():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload_with_decoys_first(root)
        suite_payload = _slice_grounding_gap_suite_payload()

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        row = payload["rows"][0]
        template_case = payload["template"]["cases"][0]
        selected_rule = template_case["benchmark"]["expected_rule_ids"][0]
        metadata = template_case["benchmark"]["metadata"]
        template = root / "slice_rule_diversity_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_slice_rule_diversity",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )

        assert report.generated_count == 1
        assert row["target_signal"] == "weak_slice_grounding"
        assert row["selection_policy"] == "slice_grounding_candidate_priority"
        assert selected_rule not in {
            "missing_len_zero_guard",
            "possible_index_overrun",
        }
        assert selected_rule in {
            "dict_missing_key_guard",
            "inverted_empty_guard",
            "inplace_api_return_value",
        }
        assert metadata["hard_case_target_signal"] == "weak_slice_grounding"
        assert selected_rule in payload["selection_audit"]["selected_rules"]
        assert validation.is_valid
        assert benchmark_report.patch_success_rate == 1.0
        assert benchmark_report.cases[0].localization_details[0][
            "slice_grounding"
        ]["grounded"] is True


def test_hard_case_generator_composes_two_hop_cross_function_case():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        catalog_payload = _catalog_payload(root)
        suite_payload = _suite_payload(
            bucket_counts={"easy": 8, "medium": 2, "hard": 5},
            label_counts={
                "multi_ground_truth": 2,
                "multi_patch_bundle": 2,
                "wide_beam_search": 2,
                "failed_before_success": 2,
                "reflection_depth": 2,
                "cross_file_patch": 3,
                "patch_candidate_competition": 8,
                "cross_function_trace": 0,
            },
        )

        report = generate_hard_case_candidates(
            suite_payload,
            catalog_payload,
            max_total_cases=1,
        )
        payload = report.to_dict()
        template_case = payload["template"]["cases"][0]
        template = root / "generated_cross_function_template.json"
        template.write_text(json.dumps(payload["template"]), encoding="utf-8")
        validation = BenchmarkValidator().validate_template(template)
        manifest = BenchmarkMaterializer().materialize_template(
            template,
            root / "materialized_cross_function",
        )
        benchmark_report = BenchmarkRunner(use_dynamic_coverage=False).run_manifest(
            manifest
        )
        details = benchmark_report.cases[0].localization_details[0]
        metadata = template_case["benchmark"]["metadata"]
        markdown = render_hard_case_generation_markdown(report)

        assert report.generated_count == 1
        assert report.rows[0].target_signal == "cross_function_trace"
        assert report.rows[0].strategy == "cross_file_composition"
        assert report.rows[0].wrapper_depth == 2
        assert metadata["hard_case_generated"] is True
        assert metadata["hard_case_target_signal"] == "cross_function_trace"
        assert metadata["cross_file_trace"] is True
        assert metadata["wrapper_depth"] == 2
        assert validation.is_valid
        assert benchmark_report.top1 == 1.0
        assert details["call_chain_length"] == 3
        assert "# Hard-Case Candidate Generation" in markdown


def test_hard_case_generator_cli_writes_report_and_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        suite = root / "suite.json"
        catalog = root / "catalog.json"
        output_json = root / "hard_case_generation.json"
        output_markdown = root / "hard_case_generation.md"
        output_template = root / "hard_case_template.json"
        suite.write_text(
            json.dumps(
                _suite_payload(
                    bucket_counts={"easy": 8, "medium": 2, "hard": 5},
                    label_counts={
                        "multi_ground_truth": 2,
                        "multi_patch_bundle": 0,
                        "wide_beam_search": 2,
                        "failed_before_success": 2,
                        "reflection_depth": 2,
                        "cross_file_patch": 3,
                        "patch_candidate_competition": 8,
                        "cross_function_trace": 8,
                    },
                )
            ),
            encoding="utf-8",
        )
        catalog.write_text(json.dumps(_catalog_payload(root)), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.hard_case_generator",
                str(suite),
                str(catalog),
                "--max-total-cases",
                "1",
                "--format",
                "markdown",
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report_payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert "# Hard-Case Candidate Generation" in completed.stdout
        assert report_payload["generated_count"] == 1
        assert "multi_bug_composition" in output_markdown.read_text(encoding="utf-8")
        assert BenchmarkValidator().validate_template(output_template).is_valid


def test_hard_case_generator_cli_skips_empty_template_output():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        suite = root / "suite.json"
        catalog = root / "catalog.json"
        output_json = root / "hard_case_generation.json"
        output_template = root / "hard_case_template.json"
        suite.write_text(
            json.dumps(
                _suite_payload(
                    bucket_counts={"easy": 8, "medium": 2, "hard": 5},
                    label_counts={
                        "multi_ground_truth": 2,
                        "multi_patch_bundle": 2,
                        "wide_beam_search": 2,
                        "failed_before_success": 2,
                        "reflection_depth": 2,
                        "cross_file_patch": 3,
                        "patch_candidate_competition": 8,
                        "cross_function_trace": 8,
                    },
                )
            ),
            encoding="utf-8",
        )
        catalog.write_text(json.dumps(_catalog_payload(root)), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.hard_case_generator",
                str(suite),
                str(catalog),
                "--output-json",
                str(output_json),
                "--output-template",
                str(output_template),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        report_payload = json.loads(output_json.read_text(encoding="utf-8"))

        assert completed.returncode == 0
        assert report_payload["generated_count"] == 0
        assert not output_template.exists()
        assert "No generated cases" in completed.stderr


def _suite_payload(
    *,
    bucket_counts: dict[str, int],
    label_counts: dict[str, int],
) -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": sum(bucket_counts.values()),
                "difficulty_report": {
                    "bucket_counts": bucket_counts,
                    "label_counts": label_counts,
                    "cases": [],
                },
            },
            "cases": [],
        }
    }


def _ablation_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
            },
            "cases": [],
        },
        "ablation_impact": {
            "baseline_variant": "full",
            "impacted_variant_count": 2,
            "rows": [
                {
                    "variant": "without_static_rules",
                    "direction": "regression",
                    "impact_score": -0.2,
                },
                {
                    "variant": "without_beam_search",
                    "direction": "regression",
                    "impact_score": -0.0571,
                },
            ],
        },
    }


def _direct_search_repair_ablation_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
            },
            "cases": [],
        },
        "ablation_impact": {
            "baseline_variant": "full",
            "impacted_variant_count": 4,
            "rows": [
                {
                    "variant": "without_reflection",
                    "direction": "regression",
                    "impact_score": -0.16,
                },
                {
                    "variant": "without_patch_prior",
                    "direction": "regression",
                    "impact_score": -0.12,
                },
                {
                    "variant": "without_diversity_reranking",
                    "direction": "regression",
                    "impact_score": -0.11,
                },
                {
                    "variant": "without_candidate_deduplication",
                    "direction": "regression",
                    "impact_score": -0.10,
                },
            ],
        },
    }


def _static_rule_gap_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
            },
            "cases": [],
        },
        "ablation_impact": {
            "baseline_variant": "full",
            "impacted_variant_count": 1,
            "rows": [
                {
                    "variant": "without_static_rules",
                    "direction": "regression",
                    "impact_score": -0.2,
                },
            ],
        },
    }


def _data_dependency_ablation_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
            },
            "cases": [],
        },
        "ablation_impact": {
            "baseline_variant": "full",
            "impacted_variant_count": 1,
            "rows": [
                {
                    "variant": "without_data_dependency",
                    "direction": "regression",
                    "impact_score": -0.18,
                },
            ],
        },
    }


def _multi_patch_ablation_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
            },
            "cases": [],
        },
        "ablation_impact": {
            "baseline_variant": "full",
            "impacted_variant_count": 1,
            "rows": [
                {
                    "variant": "without_multi_patch_repair",
                    "direction": "regression",
                    "impact_score": -0.22,
                },
            ],
        },
    }


def _graph_bundle_ablation_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
            },
            "cases": [],
        },
        "ablation_impact": {
            "baseline_variant": "full",
            "impacted_variant_count": 1,
            "rows": [
                {
                    "variant": "without_graph_bundle_search",
                    "direction": "regression",
                    "impact_score": -0.24,
                },
            ],
        },
    }


def _rule_precision_filter_ablation_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
            },
            "cases": [],
        },
        "ablation_impact": {
            "baseline_variant": "full",
            "impacted_variant_count": 1,
            "rows": [
                {
                    "variant": "without_rule_precision_filter",
                    "direction": "regression",
                    "impact_score": -0.19,
                },
            ],
        },
    }


def _reflection_ablation_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
            },
            "cases": [],
        },
        "ablation_impact": {
            "baseline_variant": "full",
            "impacted_variant_count": 1,
            "rows": [
                {
                    "variant": "without_reflection",
                    "direction": "regression",
                    "impact_score": -0.16,
                },
            ],
        },
    }


def _subscript_key_flow_gap_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 12,
                "data_flow_evidence_case_count": 8,
                "subscript_key_flow_case_count": 0,
                "average_top1_data_dependency": 0.15,
            },
            "cases": [
                {
                    "case_name": "no_key_flow_case",
                    "localization_details": [
                        {
                            "function_name": "sample.no_key_flow",
                            "data_flow_evidence": {
                                "internal_edges": 1,
                                "key_flow_edges": 0,
                                "cross_function_edges": 0,
                                "total_edges": 1,
                            },
                        }
                    ],
                }
            ],
        }
    }


def _cross_function_data_flow_gap_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 12,
                "data_flow_evidence_case_count": 8,
                "cross_function_data_flow_case_count": 0,
                "average_top1_data_dependency": 0.15,
            },
            "cases": [
                {
                    "case_name": "no_cross_function_flow_case",
                    "localization_details": [
                        {
                            "function_name": "sample.direct_target",
                            "data_flow_evidence": {
                                "internal_edges": 1,
                                "key_flow_edges": 0,
                                "cross_function_edges": 0,
                                "total_edges": 1,
                            },
                        }
                    ],
                }
            ],
        }
    }


def _search_competition_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
                "search_competition_analysis": {
                    "beam_case_count": 62,
                    "multi_candidate_case_count": 18,
                    "score_inversion_count": 0,
                    "average_failure_pressure": 0.2,
                    "rows": [
                        {
                            "case": "candidate_pressure_case",
                            "evaluated_nodes": 4,
                            "failure_pressure": 0.75,
                            "score_inversion": False,
                        }
                    ],
                },
            },
            "cases": [],
        }
    }


def _search_competition_full_gap_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
                "search_competition_analysis": {
                    "beam_case_count": 62,
                    "multi_candidate_case_count": 8,
                    "score_inversion_count": 0,
                    "average_failure_pressure": 0.05,
                    "diversity_assisted_success_count": 1,
                    "average_success_diversity_lift": 1.0,
                    "rows": [],
                },
            },
            "cases": [],
        }
    }


def _search_diversity_reranking_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
                "search_competition_analysis": {
                    "beam_case_count": 62,
                    "multi_candidate_case_count": 18,
                    "score_inversion_count": 2,
                    "average_failure_pressure": 0.2,
                    "diversity_assisted_success_count": 0,
                    "average_success_diversity_lift": 0.0,
                    "rows": [],
                },
            },
            "cases": [],
        }
    }


def _candidate_deduplication_budget_gap_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
                "search_budget_analysis": {
                    "case_count": 62,
                    "evaluated_case_count": 62,
                    "dedupe_affected_case_count": 0,
                    "total_deduplicated_candidates": 0,
                    "average_duplicate_pressure": 0.0,
                    "rows": [],
                },
            },
            "cases": [],
        }
    }


def _reflection_gap_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 1,
                "reflection_analysis": {
                    "case_count": 1,
                    "reflection_case_count": 0,
                    "reflection_success_case_count": 0,
                    "reflection_candidate_count": 0,
                    "successful_reflection_candidate_count": 0,
                    "rows": [],
                },
            },
            "cases": [],
        }
    }


def _search_candidate_competition_only_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 62,
                "search_competition_analysis": {
                    "beam_case_count": 62,
                    "multi_candidate_case_count": 0,
                    "score_inversion_count": 2,
                    "average_failure_pressure": 0.2,
                    "rows": [],
                },
            },
            "cases": [],
        }
    }


def _slice_grounding_gap_suite_payload() -> dict:
    return {
        "benchmark_report": {
            "summary": {
                "case_count": 10,
                "slice_grounded_case_count": 4,
                "average_top1_slice_support": 0.95,
                "average_top1_slice_failed_test_reachability": 1.0,
                "average_top1_slice_call_chain_coverage": 1.0,
            },
            "cases": [
                {
                    "case_name": "weak_slice_case",
                    "localization_details": [
                        {
                            "function_name": "service.target",
                            "slice_grounding": {
                                "support_score": 0.20,
                                "failed_test_reachability": 1.0,
                                "call_chain_edge_coverage": 1.0,
                            },
                        }
                    ],
                }
            ],
        }
    }


def _catalog_payload(root: Path) -> dict:
    average_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_average_mean(root)),
                    "target_path": "average_mean.py",
                }
            ]
        },
        recipe="missing_len_zero_guard",
    )
    bubble_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_bubble_sort(root)),
                    "target_path": "bubble_sort.py",
                }
            ]
        },
        recipe="possible_index_overrun",
    )
    return {
        "candidates": [
            *average_report.to_dict()["catalog"]["candidates"],
            *bubble_report.to_dict()["catalog"]["candidates"],
        ]
    }


def _catalog_payload_with_two_index_candidates(root: Path) -> dict:
    first_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_bubble_sort(root)),
                    "target_path": "bubble_sort.py",
                }
            ]
        },
        recipe="possible_index_overrun",
    )
    second_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_bubble_sort_variant(root)),
                    "target_path": "bubble_sort_variant.py",
                }
            ]
        },
        recipe="possible_index_overrun",
    )
    static_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_average_mean(root)),
                    "target_path": "average_mean.py",
                }
            ]
        },
        recipe="missing_len_zero_guard",
    )
    return {
        "candidates": [
            *static_report.to_dict()["catalog"]["candidates"],
            *first_report.to_dict()["catalog"]["candidates"],
            *second_report.to_dict()["catalog"]["candidates"],
        ]
    }


def _catalog_payload_with_dict_candidate(root: Path) -> dict:
    dict_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_score_lookup(root)),
                    "target_path": "score_lookup.py",
                }
            ]
        },
        recipe="dict_missing_key_guard",
    )
    return {"candidates": dict_report.to_dict()["catalog"]["candidates"]}


def _catalog_payload_with_single_index_search_decoys(root: Path) -> dict:
    index_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_bubble_sort(root)),
                    "target_path": "bubble_sort.py",
                }
            ]
        },
        recipe="possible_index_overrun",
    )
    inplace_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_sorted_copy(root)),
                    "target_path": "sorted_copy.py",
                }
            ]
        },
        recipe="inplace_api_return_value",
    )
    broad_exception_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_bubble_sort_variant(root)),
                    "target_path": "bubble_sort_variant.py",
                }
            ]
        },
        recipe="broad_exception_pass",
    )
    mutable_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_mean_value(root)),
                    "target_path": "mean_value.py",
                }
            ]
        },
        recipe="mutable_default_arg",
    )
    return {
        "candidates": [
            *index_report.to_dict()["catalog"]["candidates"],
            *inplace_report.to_dict()["catalog"]["candidates"],
            *broad_exception_report.to_dict()["catalog"]["candidates"],
            *mutable_report.to_dict()["catalog"]["candidates"],
        ]
    }


def _catalog_payload_with_decoys_first(root: Path) -> dict:
    sorted_copy_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_sorted_copy(root)),
                    "target_path": "sorted_copy.py",
                }
            ]
        },
        recipe="inplace_api_return_value",
    )
    mutable_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(_write_mean_value(root)),
                    "target_path": "mean_value.py",
                }
            ]
        },
        recipe="mutable_default_arg",
    )
    base = _catalog_payload(root)
    return {
        "candidates": [
            *sorted_copy_report.to_dict()["catalog"]["candidates"],
            *mutable_report.to_dict()["catalog"]["candidates"],
            *base["candidates"],
        ]
    }


def _rename_candidate(candidate: dict, candidate_id: str) -> dict:
    output = json.loads(json.dumps(candidate))
    output["id"] = candidate_id
    case = output["template_case"]
    case["name"] = candidate_id
    case["repo_path"] = f"{candidate_id}_repo"
    return output


def _with_complete_provenance(candidate: dict) -> dict:
    output = json.loads(json.dumps(candidate))
    case = output["template_case"]
    source_path = f"Lib/{case['sources'][0]['target_path']}"
    for source in case.get("sources", []):
        source.update(
            {
                "owner": "python",
                "repo": "cpython",
                "ref": "3.12.0",
                "source_path": source_path,
                "sha256": "a" * 64,
                "license": "PSF-2.0",
            }
        )
    metadata = case["benchmark"]["metadata"]
    metadata.update(
        {
            "source": "github_raw_recipe_generation",
            "upstream": "python/cpython",
            "upstream_ref": "3.12.0",
            "upstream_path": source_path,
            "license": "PSF-2.0",
            "materialized_mutations": case.get("mutations", []),
        }
    )
    return output


def _write_average_mean(root: Path) -> Path:
    raw_source = root / "average_mean.py"
    raw_source.write_text(
        "def mean(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    return raw_source


def _write_sorted_copy(root: Path) -> Path:
    raw_source = root / "sorted_copy.py"
    raw_source.write_text(
        "def sorted_copy(values):\n"
        "    result = sorted(values)\n"
        "    return result\n",
        encoding="utf-8",
    )
    return raw_source


def _write_mean_value(root: Path) -> Path:
    raw_source = root / "mean_value.py"
    raw_source.write_text(
        "def mean_value(nums):\n"
        "    if not nums:\n"
        "        raise ValueError(\"List is empty\")\n"
        "    return sum(nums) / len(nums)\n",
        encoding="utf-8",
    )
    return raw_source


def _write_score_lookup(root: Path) -> Path:
    raw_source = root / "score_lookup.py"
    raw_source.write_text(
        "def score_for(scores, name):\n"
        "    return scores.get(name, 0)\n",
        encoding="utf-8",
    )
    return raw_source


def _write_bubble_sort(root: Path) -> Path:
    raw_source = root / "bubble_sort.py"
    raw_source.write_text(
        "def bubble_sort_recursive(collection):\n"
        "    length = len(collection)\n"
        "    for i in range(length - 1):\n"
        "        if collection[i] > collection[i + 1]:\n"
        "            collection[i], collection[i + 1] = collection[i + 1], collection[i]\n"
        "    if length <= 1:\n"
        "        return collection\n"
        "    return bubble_sort_recursive(collection[:-1]) + [collection[-1]]\n",
        encoding="utf-8",
    )
    return raw_source


def _write_bubble_sort_variant(root: Path) -> Path:
    raw_source = root / "bubble_sort_variant.py"
    raw_source.write_text(
        "def bubble_sort_recursive_variant(collection):\n"
        "    length = len(collection)\n"
        "    for i in range(length - 1):\n"
        "        if collection[i] > collection[i + 1]:\n"
        "            collection[i], collection[i + 1] = collection[i + 1], collection[i]\n"
        "    if length <= 1:\n"
        "        return collection\n"
        "    return bubble_sort_recursive_variant(collection[:-1]) + [collection[-1]]\n",
        encoding="utf-8",
    )
    return raw_source
