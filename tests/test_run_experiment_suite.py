import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from code_intelligence_agent.evaluation.benchmark_multi_bug_composer import (
    compose_multi_bug_benchmarks,
)
from code_intelligence_agent.evaluation.benchmark_recipe_generator import (
    generate_benchmark_recipes,
)
from code_intelligence_agent.evaluation.benchmark_validator import BenchmarkValidator
from code_intelligence_agent.evaluation.quality_gate import QualityGateThresholds
from code_intelligence_agent.evaluation.refresh_suite_weight_search import (
    refresh_suite_weight_search,
)
from code_intelligence_agent.evaluation.run_experiment_suite import (
    run_experiment_suite,
)


def test_experiment_suite_writes_json_and_markdown_artifacts():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        template = _write_shift_left_template(root)
        output = root / "suite"
        readme = root / "README.MD"
        readme.write_text(
            Path("README.MD").read_text(encoding="utf-8").replace(
                "| Benchmark Cases | 62 |",
                "| Benchmark Cases | 0 |",
            ),
            encoding="utf-8",
        )

        result = run_experiment_suite(
            template_path=template,
            output_dir=output,
            use_dynamic_coverage=False,
            weight_search_top_n=2,
            run_quality_gate=True,
            run_showcase_report=True,
            quality_gate_thresholds=QualityGateThresholds(
                min_cases=1,
                min_difficulty_medium_cases=0,
                min_difficulty_hard_cases=0,
                min_difficulty_cross_file_patch_cases=0,
                min_difficulty_patch_competition_cases=0,
                min_difficulty_cross_function_data_flow_cases=0,
                min_bug_type_count=1,
                min_expected_rule_count=1,
                min_cases_per_bug_type=1,
                min_cases_per_expected_rule=1,
                min_generalization_source_groups=1,
                min_generalization_holdout_cases=0,
                min_localization_source_holdout_splits=0,
                min_localization_holdout_train_cases=0,
                min_benchmark_provenance_case_coverage=0.0,
                min_benchmark_provenance_mutation_coverage=0.0,
                min_benchmark_provenance_source_sha_coverage=0.0,
                min_benchmark_provenance_stable_ref_coverage=0.0,
                min_benchmark_provenance_license_coverage=0.0,
                max_benchmark_provenance_duplicate_signatures=1,
                max_benchmark_provenance_source_concentration=1.0,
                max_benchmark_provenance_leakage_risk_score=1.0,
            ),
            sync_readme_showcase_path=readme,
        )

        json_path = Path(result["suite_json_path"])
        markdown_path = Path(result["suite_markdown_path"])
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        markdown = markdown_path.read_text(encoding="utf-8")

        assert json_path.exists()
        assert markdown_path.exists()
        assert Path(payload["manifest_path"]).exists()
        assert payload["benchmark_report"]["summary"]["case_count"] == 1
        assert payload["benchmark_report"]["summary"]["patch_success_rate"] == 1.0
        assert payload["llm_config_audit"]["enabled_roles"] == []
        assert payload["llm_config_audit"]["configuration_complete"] is True
        assert len(payload["llm_config_audit"]["roles"]) == 3
        assert any(
            item["variant"] == "full"
            for item in payload["ablation_results"]
        )
        assert payload["ablation_impact"]["baseline_variant"] == "full"
        assert payload["ablation_impact"]["impacted_variant_count"] >= 1
        assert payload["ablation_impact"]["rows"]
        assert len(payload["weight_search_results"]) == 2
        assert payload["weight_search_results"][0]["case_count"] == 1
        assert payload["weight_search_results"][0]["pareto_optimal"] is True
        assert len(payload["patch_weight_search_results"]) == 2
        assert payload["patch_weight_search_results"][0]["case_count"] == 1
        assert payload["patch_weight_search_results"][0]["pareto_optimal"] is True
        assert payload["patch_judge_fusion_summary"]["status"] == "not_evaluated"
        assert payload["patch_judge_fusion_summary"]["judge_profile_count"] == 0
        assert payload["hard_case_mining"]["suggestion_count"] >= 1
        assert payload["hard_case_mining"]["suggestions"]
        assert payload["benchmark_mining"]["judged_candidate_count"] == 0
        assert payload["benchmark_mining"]["cluster_count"] == 0
        assert Path(payload["benchmark_mining_json_path"]).exists()
        assert Path(payload["benchmark_mining_markdown_path"]).exists()
        assert payload["benchmark_mining_template_seeds_path"] == ""
        assert payload["quality_gate"]["thresholds"]["min_cases"] == 1
        assert payload["quality_gate"]["passed"] is True
        assert payload["settings"]["run_showcase_report"] is True
        assert payload["settings"]["sync_readme_showcase_path"] == str(readme)
        assert payload["showcase_report"]["readiness_score"] == 100.0
        assert Path(payload["showcase_report_json_path"]).exists()
        assert Path(payload["showcase_report_markdown_path"]).exists()
        assert Path(payload["resume_showcase_markdown_path"]).exists()
        assert payload["readme_showcase_sync_path"] == str(readme)
        assert payload["readme_showcase_sync_changed"] is True
        assert payload["readme_showcase_sync_initial_mismatch_count"] >= 1
        assert payload["readme_showcase_sync_mismatch_count"] == 0
        assert "| Benchmark Cases | 1 |" in readme.read_text(encoding="utf-8")
        assert "# Code Intelligence Agent Resume Showcase" in Path(
            payload["resume_showcase_markdown_path"]
        ).read_text(encoding="utf-8")
        assert "## LLM Configuration Audit" in markdown
        assert any(
            check["name"] == "benchmark_cases"
            for check in payload["quality_gate"]["checks"]
        )
        assert any(
            check["name"] == "difficulty_case_coverage"
            for check in payload["quality_gate"]["checks"]
        )
        assert any(
            check["name"] == "bug_type_count"
            for check in payload["quality_gate"]["checks"]
        )
        assert any(
            check["name"] == "expected_rule_count"
            for check in payload["quality_gate"]["checks"]
        )
        assert any(
            check["name"] == "generalization_source_groups"
            for check in payload["quality_gate"]["checks"]
        )
        assert any(
            check["name"] == "final_score_weight_robust_score"
            for check in payload["quality_gate"]["checks"]
        )
        assert any(
            check["name"] == "final_score_weight_top1_gap"
            for check in payload["quality_gate"]["checks"]
        )
        assert any(
            check["name"] == "final_score_weight_pareto_optimal"
            for check in payload["quality_gate"]["checks"]
        )
        assert any(
            check["name"] == "patch_score_weight_pareto_optimal"
            for check in payload["quality_gate"]["checks"]
        )
        assert "# Experiment Suite" in markdown
        assert "## Benchmark" in markdown
        assert "## Ablation Study" in markdown
        assert "## Ablation Impact" in markdown
        assert "## FinalScore Weight Search" in markdown
        assert "## PatchScore Weight Search" in markdown
        assert "PatchScore Pareto Frontier" in markdown
        assert "# Hard-Case Mining" in markdown
        assert "# Benchmark Mining" in markdown
        assert "# Quality Gate" in markdown
        assert "# Algorithm Showcase Report" in markdown


def test_experiment_suite_cli_outputs_json_and_writes_artifacts():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        template = _write_shift_left_template(root)
        catalog = root / "hard_case_catalog.json"
        catalog.write_text(json.dumps(_hard_case_catalog_payload(root)), encoding="utf-8")
        output = root / "suite_cli"
        readme = root / "README.MD"
        readme.write_text(
            Path("README.MD").read_text(encoding="utf-8").replace(
                "| Benchmark Cases | 62 |",
                "| Benchmark Cases | 0 |",
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "code_intelligence_agent.evaluation.run_experiment_suite",
                str(template),
                str(output),
                "--format",
                "json",
                "--no-dynamic-coverage",
                "--top-n",
                "1",
                "--run-quality-gate",
                "--run-showcase-report",
                "--sync-readme-showcase",
                str(readme),
                "--quality-gate-min-cases",
                "1",
                "--quality-gate-min-slice-grounded-case-ratio",
                "0",
                "--quality-gate-min-average-top1-slice-support",
                "0",
                "--quality-gate-min-average-top1-slice-failed-test-reachability",
                "0",
                "--quality-gate-min-average-top1-slice-call-chain-coverage",
                "0",
                "--quality-gate-min-llm-judge-cases",
                "0",
                "--quality-gate-min-patch-judge-candidates",
                "0",
                "--quality-gate-max-patch-judge-fusion-validation-regression",
                "0.50",
                "--quality-gate-max-patch-judge-fusion-top1-regression",
                "0.50",
                "--quality-gate-max-search-budget-first-success-rank-p90",
                "9",
                "--quality-gate-min-search-competition-multi-candidate-cases",
                "0",
                "--quality-gate-min-search-competition-multi-candidate-rule-diversity",
                "0",
                "--quality-gate-min-search-competition-multi-candidate-failure-type-diversity",
                "0",
                "--quality-gate-min-search-competition-multi-candidate-retention-bucket-diversity",
                "0",
                "--quality-gate-min-search-competition-diversity-assisted-successes",
                "0",
                "--quality-gate-min-search-competition-average-success-diversity-lift",
                "0",
                "--quality-gate-min-search-competition-average-success-diversity-bonus",
                "0",
                "--quality-gate-min-metric-uncertainty-cases",
                "1",
                "--quality-gate-max-metric-uncertainty-top1-width",
                "0.90",
                "--quality-gate-max-metric-uncertainty-map-width",
                "0.91",
                "--quality-gate-max-metric-uncertainty-patch-success-width",
                "0.92",
                "--quality-gate-min-metric-uncertainty-top1-lower",
                "0.10",
                "--quality-gate-min-metric-uncertainty-map-lower",
                "0.11",
                "--quality-gate-min-metric-uncertainty-patch-success-lower",
                "0.12",
                "--quality-gate-min-difficulty-medium-cases",
                "0",
                "--quality-gate-min-difficulty-hard-cases",
                "0",
                "--quality-gate-min-difficulty-cross-file-patch-cases",
                "0",
                "--quality-gate-min-difficulty-patch-competition-cases",
                "0",
                "--quality-gate-min-difficulty-cross-function-data-flow-cases",
                "0",
                "--quality-gate-min-bug-type-count",
                "1",
                "--quality-gate-min-expected-rule-count",
                "1",
                "--quality-gate-min-cases-per-bug-type",
                "1",
                "--quality-gate-min-cases-per-expected-rule",
                "1",
                "--quality-gate-min-generalization-source-groups",
                "1",
                "--quality-gate-min-generalization-holdout-cases",
                "0",
                "--quality-gate-min-localization-source-holdout-splits",
                "0",
                "--quality-gate-min-localization-holdout-train-cases",
                "0",
                "--quality-gate-min-benchmark-provenance-case-coverage",
                "0",
                "--quality-gate-min-benchmark-provenance-mutation-coverage",
                "0",
                "--quality-gate-min-benchmark-provenance-source-sha-coverage",
                "0",
                "--quality-gate-min-benchmark-provenance-stable-ref-coverage",
                "0",
                "--quality-gate-min-benchmark-provenance-license-coverage",
                "0",
                "--quality-gate-max-benchmark-provenance-duplicate-signatures",
                "1",
                "--quality-gate-max-benchmark-provenance-source-concentration",
                "1",
                "--quality-gate-max-benchmark-provenance-leakage-risk-score",
                "1",
                "--quality-gate-min-hard-case-generation-selected-candidates-per-case",
                "1",
                "--quality-gate-min-hard-case-generation-rule-coverage",
                "1",
                "--quality-gate-min-hard-case-generation-function-coverage",
                "1",
                "--quality-gate-min-hard-case-generation-source-coverage",
                "1",
                "--quality-gate-min-hard-case-generation-candidate-score",
                "0",
                "--quality-gate-min-hard-case-generation-diversity-bonus",
                "0",
                "--quality-gate-min-hard-case-generation-provenance-selected-ratio",
                "0",
                "--quality-gate-min-hard-case-generation-provenance-bonus",
                "0",
                "--quality-gate-min-hard-case-generation-provenance-source-sha-coverage",
                "0",
                "--quality-gate-min-hard-case-generation-provenance-stable-ref-coverage",
                "0",
                "--quality-gate-max-hard-case-generation-provenance-leakage-risk",
                "1",
                "--quality-gate-min-hard-case-generated-benchmark-cases",
                "1",
                "--quality-gate-min-hard-case-generated-patch-success-rate",
                "0",
                "--quality-gate-min-hard-case-generated-multi-candidate-cases",
                "0",
                "--quality-gate-min-hard-case-generated-score-inversions",
                "0",
                "--quality-gate-min-hard-case-generated-diversity-assisted-successes",
                "0",
                "--quality-gate-min-hard-case-generated-diversity-budget-sensitive-successes",
                "0",
                "--quality-gate-min-hard-case-generated-success-diversity-lift",
                "0",
                "--quality-gate-min-hard-case-generated-success-diversity-bonus",
                "0",
                "--quality-gate-min-hard-case-generated-dedupe-affected-cases",
                "0",
                "--quality-gate-min-hard-case-generated-deduplicated-candidates",
                "0",
                "--quality-gate-min-hard-case-generated-duplicate-pressure",
                "0",
                "--quality-gate-min-hard-case-generated-reflection-success-cases",
                "0",
                "--quality-gate-min-hard-case-generated-reflection-candidates",
                "0",
                "--hard-case-catalog",
                str(catalog),
                "--hard-case-max-total-cases",
                "1",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(completed.stdout)

        assert completed.returncode == 0
        assert Path(payload["suite_json_path"]).exists()
        assert Path(payload["suite_markdown_path"]).exists()
        assert payload["benchmark_report"]["summary"]["top1"] == 1.0
        assert payload["benchmark_report"]["summary"]["map"] == 1.0
        assert payload["quality_gate"]["thresholds"]["min_cases"] == 1
        assert (
            payload["quality_gate"]["thresholds"]["min_slice_grounded_case_ratio"]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"]["min_average_top1_slice_support"]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_average_top1_slice_failed_test_reachability"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_average_top1_slice_call_chain_coverage"
            ]
            == 0.0
        )
        assert payload["quality_gate"]["thresholds"]["min_llm_judge_cases"] == 0
        assert (
            payload["quality_gate"]["thresholds"]["min_patch_judge_candidates"]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "max_patch_judge_fusion_validation_regression"
            ]
            == 0.50
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "max_patch_judge_fusion_top1_regression"
            ]
            == 0.50
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "max_search_budget_first_success_rank_p90"
            ]
            == 9.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_search_competition_multi_candidate_cases"
            ]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_search_competition_multi_candidate_rule_diversity"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_search_competition_multi_candidate_failure_type_diversity"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_search_competition_multi_candidate_retention_bucket_diversity"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_search_competition_diversity_assisted_successes"
            ]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_search_competition_average_success_diversity_lift"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_search_competition_average_success_diversity_bonus"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_metric_uncertainty_cases"
            ]
            == 1
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "max_metric_uncertainty_top1_width"
            ]
            == 0.90
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "max_metric_uncertainty_map_width"
            ]
            == 0.91
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "max_metric_uncertainty_patch_success_width"
            ]
            == 0.92
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_metric_uncertainty_top1_lower"
            ]
            == 0.10
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_metric_uncertainty_map_lower"
            ]
            == 0.11
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_metric_uncertainty_patch_success_lower"
            ]
            == 0.12
        )
        assert payload["quality_gate"]["thresholds"]["min_bug_type_count"] == 1
        assert payload["quality_gate"]["thresholds"]["min_expected_rule_count"] == 1
        assert (
            payload["quality_gate"]["thresholds"]["min_generalization_source_groups"]
            == 1
        )
        assert (
            payload["quality_gate"]["thresholds"]["min_weight_search_source_groups"]
            == 1
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_localization_source_holdout_splits"
            ]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_localization_holdout_train_cases"
            ]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generation_selected_candidates_per_case"
            ]
            == 1
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generation_candidate_score"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generation_provenance_selected_ratio"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generation_provenance_bonus"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "max_hard_case_generation_provenance_leakage_risk"
            ]
            == 1.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_benchmark_cases"
            ]
            == 1
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_patch_success_rate"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_multi_candidate_cases"
            ]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_score_inversions"
            ]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_diversity_assisted_successes"
            ]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_diversity_budget_sensitive_successes"
            ]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_success_diversity_lift"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_success_diversity_bonus"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_dedupe_affected_cases"
            ]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_deduplicated_candidates"
            ]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_duplicate_pressure"
            ]
            == 0.0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_reflection_success_cases"
            ]
            == 0
        )
        assert (
            payload["quality_gate"]["thresholds"][
                "min_hard_case_generated_reflection_candidates"
            ]
            == 0
        )
        assert payload["quality_gate"]["passed"] is True
        assert payload["settings"]["run_showcase_report"] is True
        assert payload["settings"]["sync_readme_showcase_path"] == str(readme)
        assert payload["showcase_report"]["artifact_kind"] == "experiment_suite"
        assert Path(payload["showcase_report_json_path"]).exists()
        assert Path(payload["showcase_report_markdown_path"]).exists()
        assert Path(payload["resume_showcase_markdown_path"]).exists()
        assert payload["readme_showcase_sync_path"] == str(readme)
        assert payload["readme_showcase_sync_changed"] is True
        assert "| Benchmark Cases | 1 |" in readme.read_text(encoding="utf-8")
        assert payload["hard_case_mining"]["suggestion_count"] >= 1
        assert payload["benchmark_mining"]["judged_candidate_count"] == 0
        assert Path(payload["benchmark_mining_json_path"]).exists()
        assert Path(payload["benchmark_mining_markdown_path"]).exists()
        assert payload["hard_case_generation"]["generated_count"] == 1
        assert Path(payload["hard_case_generation_json_path"]).exists()
        assert Path(payload["hard_case_generation_markdown_path"]).exists()
        assert Path(payload["hard_case_generated_template_path"]).exists()
        assert payload["settings"]["run_hard_case_generated_benchmark"] is True
        assert payload["hard_case_generated_benchmark"] is not None
        assert Path(payload["hard_case_generated_benchmark_dir"]).exists()
        assert Path(
            payload["hard_case_generated_benchmark"]["manifest_path"]
        ).exists()
        assert Path(
            payload["hard_case_generated_benchmark"]["report_artifacts"]["json"]
        ).exists()
        assert (
            payload["hard_case_generated_benchmark"]["benchmark_report"]["summary"][
                "case_count"
            ]
            == 1
        )
        assert len(payload["weight_search_results"]) == 1
        assert len(payload["patch_weight_search_results"]) == 1
        assert any(
            item["variant"] == "without_beam_search"
            for item in payload["ablation_results"]
        )


def test_experiment_suite_reuses_external_source_cache_without_fetching():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        source = (
            "def shift_left(values):\n"
            "    for i in range(len(values) - 1):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n"
        )
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        shared_cache = root / "shared_cache"
        shared_cache.mkdir()
        (shared_cache / f"{digest}.py").write_bytes(source.encode("utf-8"))
        template = root / "cached_template.json"
        template.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "name": "cached_suite_shift_left",
                            "repo_path": "cached_suite_shift_left_repo",
                            "sources": [
                                {
                                    "raw_url": "https://example.invalid/raw_sample.py",
                                    "target_path": "sample.py",
                                    "sha256": digest,
                                }
                            ],
                            "mutations": [
                                {
                                    "target_path": "sample.py",
                                    "find": "range(len(values) - 1)",
                                    "replace": "range(len(values))",
                                }
                            ],
                            "files": [
                                {
                                    "target_path": "test_sample.py",
                                    "content": (
                                        "from sample import shift_left\n\n"
                                        "def test_shift_left():\n"
                                        "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n"
                                    ),
                                }
                            ],
                            "benchmark": {
                                "buggy_functions": ["shift_left"],
                                "expected_rule_ids": ["possible_index_overrun"],
                                "failing_tests": ["test_shift_left"],
                                "passed_tests": [],
                                "test_args": [],
                                "metadata": {
                                    "source": "cached_raw_source_mutation",
                                    "bug_type": "boundary error",
                                },
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = run_experiment_suite(
            template_path=template,
            output_dir=root / "suite_cached",
            use_dynamic_coverage=False,
            run_ablation=False,
            run_weight_search=False,
            run_patch_weight_search=False,
            source_cache_dir=shared_cache,
        )
        payload = json.loads(
            Path(result["suite_json_path"]).read_text(encoding="utf-8")
        )

        assert payload["settings"]["source_cache_dir"] == str(shared_cache)
        assert payload["benchmark_report"]["summary"]["patch_success_rate"] == 1.0
        assert payload["ablation_results"] == []
        assert payload["weight_search_results"] == []
        assert payload["patch_weight_search_results"] == []
        assert "https://example.invalid" in template.read_text(encoding="utf-8")


def test_refresh_suite_weight_search_backfills_robust_quality_gate_fields():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        template = _write_shift_left_template(root)
        output = root / "suite_refresh"

        result = run_experiment_suite(
            template_path=template,
            output_dir=output,
            use_dynamic_coverage=False,
            weight_search_top_n=1,
            run_quality_gate=True,
            run_showcase_report=True,
            quality_gate_thresholds=QualityGateThresholds(
                min_cases=1,
                min_difficulty_medium_cases=0,
                min_difficulty_hard_cases=0,
                min_difficulty_cross_file_patch_cases=0,
                min_difficulty_patch_competition_cases=0,
                min_difficulty_cross_function_data_flow_cases=0,
                min_bug_type_count=1,
                min_expected_rule_count=1,
                min_cases_per_bug_type=1,
                min_cases_per_expected_rule=1,
                min_generalization_source_groups=1,
                min_generalization_holdout_cases=0,
                min_localization_source_holdout_splits=0,
                min_localization_holdout_train_cases=0,
                min_benchmark_provenance_case_coverage=0.0,
                min_benchmark_provenance_mutation_coverage=0.0,
                min_benchmark_provenance_source_sha_coverage=0.0,
                min_benchmark_provenance_stable_ref_coverage=0.0,
                min_benchmark_provenance_license_coverage=0.0,
                max_benchmark_provenance_duplicate_signatures=1,
                max_benchmark_provenance_source_concentration=1.0,
                max_benchmark_provenance_leakage_risk_score=1.0,
            ),
        )
        suite_path = Path(result["suite_json_path"])
        payload = json.loads(suite_path.read_text(encoding="utf-8"))
        old_result = dict(payload["weight_search_results"][0])
        old_audit = payload["benchmark_report"]["summary"][
            "benchmark_provenance_audit"
        ]
        for key in (
            "stable_ref_count",
            "floating_ref_count",
            "stable_ref_coverage",
            "floating_ref_sources",
        ):
            old_audit.pop(key, None)
        for key in (
            "robust_validation_score",
            "source_group_count",
            "min_source_group_cases",
            "source_groups",
            "holdout_splits",
            "max_top1_gap",
            "max_map_gap",
        ):
            old_result.pop(key, None)
        payload["weight_search_results"] = [old_result]
        payload["quality_gate"]["passed"] = False
        suite_path.write_text(json.dumps(payload), encoding="utf-8")

        refreshed = refresh_suite_weight_search(
            suite_path,
            in_place=True,
            force_no_dynamic_coverage=True,
        )
        saved = json.loads(suite_path.read_text(encoding="utf-8"))

        assert refreshed["quality_gate"]["passed"] is True
        assert saved["weight_search_results"][0]["robust_validation_score"] == 1.0
        assert saved["weight_search_results"][0]["source_group_count"] == 1
        assert saved["weight_search_results"][0]["source_groups"]
        refreshed_audit = saved["benchmark_report"]["summary"][
            "benchmark_provenance_audit"
        ]
        assert "stable_ref_coverage" in refreshed_audit
        assert "floating_ref_sources" in refreshed_audit
        assert isinstance(saved["weight_search_results"][0]["holdout_splits"], list)
        assert saved["weight_search_results"][0]["pareto_optimal"] is True
        assert saved["weight_search_results"][0]["dominated_by_count"] == 0
        assert any(
            check["name"] == "final_score_weight_robust_score"
            and check["passed"]
            for check in saved["quality_gate"]["checks"]
        )
        assert any(
            check["name"] == "final_score_weight_pareto_optimal"
            and check["passed"]
            for check in saved["quality_gate"]["checks"]
        )
        assert (
            saved["showcase_report"]["algorithm_evidence"][
                "weight_and_ablation_search"
            ]["best_final_score_robust_score"]
            == 1.0
        )
        assert "Robust Score" in Path(saved["suite_markdown_path"]).read_text(
            encoding="utf-8"
        )
        assert "FinalScore Pareto Frontier" in Path(
            saved["suite_markdown_path"]
        ).read_text(encoding="utf-8")
        assert "best_final_score_robust_score" in json.dumps(
            saved["showcase_report"],
            ensure_ascii=False,
        )
        assert Path(saved["resume_showcase_markdown_path"]).exists()
        assert "# Code Intelligence Agent Resume Showcase" in Path(
            saved["resume_showcase_markdown_path"]
        ).read_text(encoding="utf-8")


def test_experiment_suite_generates_hard_case_artifacts_from_catalog():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        template = _write_shift_left_template(root)
        catalog = root / "hard_case_catalog.json"
        catalog.write_text(json.dumps(_hard_case_catalog_payload(root)), encoding="utf-8")

        result = run_experiment_suite(
            template_path=template,
            output_dir=root / "suite_hard_case_generation",
            use_dynamic_coverage=False,
            run_ablation=False,
            run_weight_search=False,
            run_patch_weight_search=False,
            hard_case_catalog_path=catalog,
            hard_case_max_total_cases=1,
        )
        payload = json.loads(
            Path(result["suite_json_path"]).read_text(encoding="utf-8")
        )
        generated_template = Path(payload["hard_case_generated_template_path"])
        generation_markdown = Path(payload["hard_case_generation_markdown_path"])

        assert payload["settings"]["hard_case_catalog_path"] == str(catalog)
        assert payload["hard_case_generation"]["suggestion_count"] >= 1
        assert payload["hard_case_generation"]["generated_count"] == 1
        assert payload["hard_case_generation"]["template"]["cases"]
        assert Path(payload["hard_case_generation_json_path"]).exists()
        assert generation_markdown.exists()
        assert generated_template.exists()
        assert payload["hard_case_generated_benchmark"] is not None
        assert Path(payload["hard_case_generated_benchmark_dir"]).exists()
        assert Path(
            payload["hard_case_generated_benchmark"]["manifest_path"]
        ).exists()
        assert (
            payload["hard_case_generated_benchmark"]["benchmark_report"]["summary"][
                "case_count"
            ]
            == 1
        )
        assert BenchmarkValidator().validate_template(generated_template).is_valid
        assert "# Hard-Case Candidate Generation" in payload["markdown"]
        assert "## Generated Hard-Case Benchmark" in payload["markdown"]
        assert "# Hard-Case Candidate Generation" in generation_markdown.read_text(
            encoding="utf-8"
        )


def test_experiment_suite_ablation_captures_cross_file_multi_bug_regression():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        template_payload = _multi_bug_cross_file_template_payload(root)
        template = root / "multi_bug_cross_file_template.json"
        template.write_text(json.dumps(template_payload), encoding="utf-8")

        result = run_experiment_suite(
            template_path=template,
            output_dir=root / "suite_multi_bug_cross_file",
            use_dynamic_coverage=False,
            run_ablation=True,
            run_weight_search=False,
            run_patch_weight_search=False,
        )
        payload = json.loads(
            Path(result["suite_json_path"]).read_text(encoding="utf-8")
        )
        variants = {
            item["variant"]: item for item in payload["ablation_results"]
        }
        benchmark = payload["benchmark_report"]["summary"]

        assert benchmark["case_count"] == 1
        assert benchmark["patch_success_rate"] == 1.0
        assert benchmark["multi_patch_success_rate"] == 1.0
        assert variants["full"]["patch_success_rate"] == 1.0
        assert variants["full"]["multi_patch_success_rate"] == 1.0
        assert variants["without_multi_patch_repair"]["patch_success_rate"] == 0.0
        assert (
            variants["without_multi_patch_repair"]["multi_patch_success_rate"]
            == 0.0
        )
        assert variants["without_graph_bundle_search"]["patch_success_rate"] == 1.0
        assert "without_multi_patch_repair" in payload["markdown"]
        assert payload["weight_search_results"] == []
        assert payload["patch_weight_search_results"] == []


def _write_shift_left_template(root: Path) -> Path:
    raw_source = root / "raw_sample.py"
    raw_source.write_text(
        "def shift_left(values):\n"
        "    for i in range(len(values) - 1):\n"
        "        values[i] = values[i + 1]\n"
        "    return values\n",
        encoding="utf-8",
    )
    template = root / "template.json"
    template.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "suite_shift_left",
                        "repo_path": "suite_shift_left_repo",
                        "sources": [
                            {
                                "raw_url": str(raw_source),
                                "target_path": "sample.py",
                            }
                        ],
                        "mutations": [
                            {
                                "target_path": "sample.py",
                                "find": "range(len(values) - 1)",
                                "replace": "range(len(values))",
                            }
                        ],
                        "files": [
                            {
                                "target_path": "test_sample.py",
                                "content": (
                                    "from sample import shift_left\n\n"
                                    "def test_shift_left():\n"
                                    "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n"
                                ),
                            }
                        ],
                        "benchmark": {
                            "buggy_functions": ["shift_left"],
                            "expected_rule_ids": ["possible_index_overrun"],
                            "failing_tests": ["test_shift_left"],
                            "passed_tests": [],
                            "test_args": [],
                            "metadata": {
                                "source": "local_raw_source_mutation",
                                "bug_type": "boundary error",
                            },
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return template


def _multi_bug_cross_file_template_payload(root: Path) -> dict:
    average = _write_average_mean(root)
    bubble = _write_bubble_sort(root)
    average_report = generate_benchmark_recipes(
        {
            "sources": [
                {
                    "raw_url": str(average),
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
                    "raw_url": str(bubble),
                    "target_path": "bubble_sort.py",
                }
            ]
        },
        recipe="possible_index_overrun",
    )
    composition = compose_multi_bug_benchmarks(
        {
            "candidates": [
                *average_report.to_dict()["catalog"]["candidates"],
                *bubble_report.to_dict()["catalog"]["candidates"],
            ]
        },
        include_rules=["missing_len_zero_guard", "possible_index_overrun"],
        wrapper_depth=2,
    )
    return composition.to_dict()["template"]


def _hard_case_catalog_payload(root: Path) -> dict:
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
