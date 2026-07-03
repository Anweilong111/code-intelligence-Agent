from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from code_intelligence_agent.agents.llm_fault_scorer import build_llm_fault_scorer
from code_intelligence_agent.core.fault_localizer import FaultLocalizer
from code_intelligence_agent.evaluation.ablation import BenchmarkAblationRunner
from code_intelligence_agent.evaluation.ablation_impact import ablation_impact_report
from code_intelligence_agent.evaluation.hard_case_mining import (
    hard_case_mining_report,
    render_hard_case_mining_markdown,
)
from code_intelligence_agent.evaluation.hard_case_generator import (
    generate_hard_case_candidates,
    render_hard_case_generation_markdown,
)
from code_intelligence_agent.evaluation.llm_config_audit import (
    render_llm_config_audit_markdown,
)
from code_intelligence_agent.agents.llm_client import llm_config_audits_for_modes
from code_intelligence_agent.evaluation.benchmark_mining import (
    mine_benchmark_template_seeds,
    render_benchmark_mining_markdown,
)
from code_intelligence_agent.evaluation.report import (
    render_ablation_markdown,
    render_benchmark_markdown,
    render_patch_weight_search_markdown,
    render_weight_search_markdown,
)
from code_intelligence_agent.evaluation.run_template_benchmark import (
    run_template_benchmark,
)
from code_intelligence_agent.evaluation.patch_weight_search import (
    PatchWeightSearchRunner,
    patch_judge_fusion_summary,
)
from code_intelligence_agent.evaluation.quality_gate import (
    QualityGateThresholds,
    evaluate_quality_gate,
    render_quality_gate_markdown,
)
from code_intelligence_agent.evaluation.readme_showcase_sync import (
    readme_showcase_mismatches,
    showcase_overview_metrics,
    sync_readme_showcase_text,
)
from code_intelligence_agent.evaluation.showcase_report import (
    build_showcase_report,
    render_resume_showcase_markdown,
    render_showcase_markdown,
)
from code_intelligence_agent.evaluation.weight_search import WeightSearchRunner
from code_intelligence_agent.search.patch_judge import build_patch_judge


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full CIA experiment suite: template materialization, benchmark, "
            "ablation study, and FinalScore weight search."
        )
    )
    parser.add_argument("template", help="Benchmark template JSON")
    parser.add_argument("output_dir", help="Directory for generated artifacts")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Stdout format. Artifacts are always written to output_dir.",
    )
    parser.add_argument(
        "--patch-mode",
        choices=["rule", "llm"],
        default="rule",
        help="Patch generation mode for the benchmark run.",
    )
    parser.add_argument(
        "--judge-mode",
        choices=["none", "llm"],
        default="none",
        help="Optional LLM-as-judge mode. The llm mode defaults to DeepSeek.",
    )
    parser.add_argument(
        "--patch-judge-mode",
        choices=["none", "llm"],
        default="none",
        help=(
            "Optional patch-level LLM judge used inside BeamSearch scoring. "
            "The llm mode defaults to DeepSeek."
        ),
    )
    parser.add_argument(
        "--llm-score-mode",
        choices=["none", "llm"],
        default="none",
        help="Optional LLMScore signal for fault localization.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of top FinalScore weight profiles to persist.",
    )
    parser.add_argument(
        "--no-dynamic-coverage",
        action="store_true",
        help="Disable pytest trace coverage and use manifest fallback coverage.",
    )
    parser.add_argument(
        "--skip-ablation",
        action="store_true",
        help="Skip ablation study for faster smoke runs.",
    )
    parser.add_argument(
        "--skip-weight-search",
        action="store_true",
        help="Skip FinalScore weight search for faster smoke runs.",
    )
    parser.add_argument(
        "--skip-patch-weight-search",
        action="store_true",
        help="Skip PatchScore weight search for faster smoke runs.",
    )
    parser.add_argument(
        "--source-cache-dir",
        help=(
            "Optional shared raw-source cache directory. Defaults to "
            "<output_dir>/materialized/.source_cache."
        ),
    )
    parser.add_argument(
        "--run-quality-gate",
        action="store_true",
        help="Embed README acceptance-gate results into suite.json and suite.md.",
    )
    parser.add_argument(
        "--run-showcase-report",
        action="store_true",
        help="Write resume-oriented showcase_report.json and showcase_report.md.",
    )
    parser.add_argument(
        "--sync-readme-showcase",
        help=(
            "Optional README path to update the project overview metrics from "
            "the generated showcase report."
        ),
    )
    parser.add_argument(
        "--hard-case-catalog",
        help=(
            "Optional recipe/source-mining catalog JSON. When provided, the "
            "suite generates hard-case candidate artifacts from mining gaps."
        ),
    )
    parser.add_argument(
        "--hard-case-max-cases-per-suggestion",
        type=int,
        default=1,
        help="Maximum generated hard-case candidates per mining suggestion.",
    )
    parser.add_argument(
        "--hard-case-max-total-cases",
        type=int,
        default=None,
        help="Optional global cap for generated hard-case candidates.",
    )
    parser.add_argument(
        "--skip-hard-case-generated-benchmark",
        action="store_true",
        help=(
            "Skip materializing and running the generated hard-case template. "
            "By default, generated hard cases are executed as a nested benchmark."
        ),
    )
    parser.add_argument(
        "--quality-gate-min-cases",
        type=int,
        default=50,
        help="Minimum benchmark case count when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-top1",
        type=float,
        default=0.65,
        help="Minimum Top-1 localization rate when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-top3",
        type=float,
        default=0.85,
        help="Minimum Top-3 localization rate when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-patch-success-rate",
        type=float,
        default=0.50,
        help="Minimum patch success rate when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-slice-grounded-case-ratio",
        type=float,
        default=0.90,
        help="Minimum slice-grounded Top-1 case ratio when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-average-top1-slice-support",
        type=float,
        default=0.70,
        help="Minimum average Top-1 slice support when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-average-top1-slice-failed-test-reachability",
        type=float,
        default=0.70,
        help=(
            "Minimum average Top-1 failed-test reachability in slice-grounded "
            "localization when --run-quality-gate is enabled."
        ),
    )
    parser.add_argument(
        "--quality-gate-min-average-top1-slice-call-chain-coverage",
        type=float,
        default=0.70,
        help=(
            "Minimum average Top-1 call-chain edge coverage in slice-grounded "
            "localization when --run-quality-gate is enabled."
        ),
    )
    parser.add_argument(
        "--quality-gate-min-weight-search-top1",
        type=float,
        default=0.50,
        help="Minimum FinalScore weight-search Top-1 when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-weight-search-robust-score",
        type=float,
        default=0.50,
        help="Minimum FinalScore weight-search robust score when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-weight-search-source-groups",
        type=int,
        default=1,
        help="Minimum FinalScore weight-search source groups when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-max-weight-search-top1-gap",
        type=float,
        default=0.20,
        help="Maximum FinalScore weight-search holdout Top-1 gap when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-max-weight-search-map-gap",
        type=float,
        default=0.20,
        help="Maximum FinalScore weight-search holdout MAP gap when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-llm-judge-cases",
        type=int,
        default=1,
        help="Minimum case-level LLM judge cases when judge mode is enabled.",
    )
    parser.add_argument(
        "--quality-gate-max-llm-judge-brier-score",
        type=float,
        default=0.25,
        help="Maximum case-level LLM judge Brier score.",
    )
    parser.add_argument(
        "--quality-gate-max-llm-judge-ece",
        type=float,
        default=0.20,
        help="Maximum case-level LLM judge expected calibration error.",
    )
    parser.add_argument(
        "--quality-gate-min-llm-judge-agreement-rate",
        type=float,
        default=0.70,
        help="Minimum case-level LLM judge evidence agreement rate.",
    )
    parser.add_argument(
        "--quality-gate-min-patch-judge-candidates",
        type=int,
        default=1,
        help="Minimum judged patch candidates when patch judge mode is enabled.",
    )
    parser.add_argument(
        "--quality-gate-max-patch-judge-brier-score",
        type=float,
        default=0.35,
        help="Maximum patch-level judge Brier score.",
    )
    parser.add_argument(
        "--quality-gate-max-patch-judge-ece",
        type=float,
        default=0.35,
        help="Maximum patch-level judge expected calibration error.",
    )
    parser.add_argument(
        "--quality-gate-min-patch-judge-agreement-rate",
        type=float,
        default=0.50,
        help="Minimum patch-level judge evidence agreement rate.",
    )
    parser.add_argument(
        "--quality-gate-max-patch-judge-fusion-validation-regression",
        type=float,
        default=0.02,
        help="Maximum allowed PatchScore validation regression from judge fusion.",
    )
    parser.add_argument(
        "--quality-gate-max-patch-judge-fusion-top1-regression",
        type=float,
        default=0.05,
        help="Maximum allowed Top-1 success regression from judge fusion.",
    )
    parser.add_argument(
        "--quality-gate-max-patch-judge-fusion-mrr-regression",
        type=float,
        default=0.05,
        help="Maximum allowed Patch MRR regression from judge fusion.",
    )
    parser.add_argument(
        "--quality-gate-max-patch-judge-fusion-success-margin-regression",
        type=float,
        default=0.05,
        help="Maximum allowed success-margin regression from judge fusion.",
    )
    parser.add_argument(
        "--quality-gate-max-patch-judge-fusion-first-success-rank-regression",
        type=float,
        default=0.25,
        help="Maximum allowed first-success-rank regression from judge fusion.",
    )
    parser.add_argument(
        "--quality-gate-min-difficulty-medium-cases",
        type=int,
        default=1,
        help="Minimum medium difficulty cases when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-difficulty-hard-cases",
        type=int,
        default=1,
        help="Minimum hard difficulty cases when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-difficulty-cross-file-patch-cases",
        type=int,
        default=1,
        help="Minimum cross-file patch cases when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-difficulty-patch-competition-cases",
        type=int,
        default=1,
        help="Minimum patch competition cases when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-difficulty-cross-function-data-flow-cases",
        type=int,
        default=1,
        help=(
            "Minimum cross-function data-flow cases when --run-quality-gate "
            "is enabled."
        ),
    )
    parser.add_argument(
        "--quality-gate-min-bug-type-count",
        type=int,
        default=6,
        help="Minimum distinct bug types when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-expected-rule-count",
        type=int,
        default=6,
        help="Minimum distinct expected rules when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-cases-per-bug-type",
        type=int,
        default=1,
        help="Minimum cases per bug type when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-cases-per-expected-rule",
        type=int,
        default=1,
        help="Minimum cases per expected rule when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-generalization-source-groups",
        type=int,
        default=3,
        help="Minimum source groups when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-generalization-holdout-cases",
        type=int,
        default=1,
        help="Minimum cases in the smallest holdout split when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-generalization-balance-entropy",
        type=float,
        default=0.50,
        help=(
            "Minimum normalized source-group balance entropy when "
            "--run-quality-gate is enabled."
        ),
    )
    parser.add_argument(
        "--quality-gate-max-generalization-top1-gap",
        type=float,
        default=0.20,
        help="Maximum train-vs-holdout Top-1 gap when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-max-generalization-map-gap",
        type=float,
        default=0.20,
        help="Maximum train-vs-holdout MAP gap when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-max-generalization-patch-success-gap",
        type=float,
        default=0.20,
        help=(
            "Maximum train-vs-holdout patch success gap when --run-quality-gate "
            "is enabled."
        ),
    )
    parser.add_argument(
        "--quality-gate-max-generalization-worst-holdout-gap-score",
        type=float,
        default=0.30,
        help=(
            "Maximum weighted worst holdout gap score when --run-quality-gate "
            "is enabled."
        ),
    )
    parser.add_argument(
        "--quality-gate-min-generalization-stability-score",
        type=float,
        default=0.70,
        help=(
            "Minimum leave-one-source-group-out stability score when "
            "--run-quality-gate is enabled."
        ),
    )
    parser.add_argument(
        "--quality-gate-min-benchmark-provenance-case-coverage",
        type=float,
        default=0.95,
        help="Minimum benchmark case provenance coverage.",
    )
    parser.add_argument(
        "--quality-gate-min-benchmark-provenance-mutation-coverage",
        type=float,
        default=0.95,
        help="Minimum materialized mutation provenance coverage.",
    )
    parser.add_argument(
        "--quality-gate-min-benchmark-provenance-source-sha-coverage",
        type=float,
        default=0.95,
        help="Minimum source SHA256 coverage when source digests are present.",
    )
    parser.add_argument(
        "--quality-gate-min-benchmark-provenance-stable-ref-coverage",
        type=float,
        default=0.95,
        help=(
            "Minimum pinned tag/commit ref coverage for benchmark source refs "
            "when --run-quality-gate is enabled."
        ),
    )
    parser.add_argument(
        "--quality-gate-min-benchmark-provenance-license-coverage",
        type=float,
        default=0.95,
        help="Minimum benchmark case license metadata coverage.",
    )
    parser.add_argument(
        "--quality-gate-max-benchmark-provenance-duplicate-signatures",
        type=int,
        default=0,
        help="Maximum duplicate benchmark bug signatures.",
    )
    parser.add_argument(
        "--quality-gate-max-benchmark-provenance-source-concentration",
        type=float,
        default=0.80,
        help="Maximum case share for the most reused source file.",
    )
    parser.add_argument(
        "--quality-gate-max-benchmark-provenance-leakage-risk-score",
        type=float,
        default=0.30,
        help="Maximum benchmark provenance leakage risk score.",
    )
    parser.add_argument(
        "--quality-gate-max-generalization-search-efficiency-gap",
        type=float,
        default=0.40,
        help=(
            "Maximum train-vs-holdout search efficiency gap when --run-quality-gate "
            "is enabled."
        ),
    )
    parser.add_argument(
        "--quality-gate-min-search-budget-cases",
        type=int,
        default=1,
        help="Minimum evaluated cases for search budget analysis.",
    )
    parser.add_argument(
        "--quality-gate-min-search-budget-success-at-1",
        type=float,
        default=0.50,
        help="Minimum Success@1 in search budget analysis.",
    )
    parser.add_argument(
        "--quality-gate-min-search-budget-auc",
        type=float,
        default=0.50,
        help="Minimum budget AUC in search budget analysis.",
    )
    parser.add_argument(
        "--quality-gate-max-search-budget-first-success-rank-p90",
        type=float,
        default=3.00,
        help="Maximum p90 first-success rank in search budget analysis.",
    )
    parser.add_argument(
        "--quality-gate-min-search-budget-dedupe-affected-cases",
        type=int,
        default=0,
        help="Minimum search-budget cases affected by candidate deduplication.",
    )
    parser.add_argument(
        "--quality-gate-min-search-budget-deduplicated-candidates",
        type=int,
        default=0,
        help="Minimum total candidates removed by search-budget deduplication.",
    )
    parser.add_argument(
        "--quality-gate-min-search-budget-average-duplicate-pressure",
        type=float,
        default=0.0,
        help="Minimum average duplicate pressure in search budget analysis.",
    )
    parser.add_argument(
        "--quality-gate-min-search-competition-multi-candidate-cases",
        type=int,
        default=1,
        help="Minimum multi-candidate cases in search competition analysis.",
    )
    parser.add_argument(
        "--quality-gate-min-search-competition-multi-candidate-rule-diversity",
        type=float,
        default=1.00,
        help="Minimum multi-candidate rule diversity in search competition analysis.",
    )
    parser.add_argument(
        "--quality-gate-min-search-competition-multi-candidate-failure-type-diversity",
        type=float,
        default=0.50,
        help="Minimum multi-candidate failure-type diversity in search competition analysis.",
    )
    parser.add_argument(
        "--quality-gate-min-search-competition-multi-candidate-retention-bucket-diversity",
        type=float,
        default=1.00,
        help="Minimum multi-candidate retention-bucket diversity in search competition analysis.",
    )
    parser.add_argument(
        "--quality-gate-min-search-competition-diversity-assisted-successes",
        type=int,
        default=0,
        help="Minimum diversity-assisted successful beam cases in search competition analysis.",
    )
    parser.add_argument(
        "--quality-gate-min-search-competition-average-success-diversity-lift",
        type=float,
        default=0.0,
        help="Minimum average base-rank lift for diversity-assisted successful candidates.",
    )
    parser.add_argument(
        "--quality-gate-min-search-competition-average-success-diversity-bonus",
        type=float,
        default=0.0,
        help="Minimum average diversity bonus on successful candidates.",
    )
    parser.add_argument(
        "--quality-gate-min-metric-uncertainty-cases",
        type=int,
        default=1,
        help="Minimum cases for bootstrap metric uncertainty analysis.",
    )
    parser.add_argument(
        "--quality-gate-max-metric-uncertainty-top1-width",
        type=float,
        default=0.40,
        help="Maximum bootstrap confidence-interval width for Top-1.",
    )
    parser.add_argument(
        "--quality-gate-max-metric-uncertainty-map-width",
        type=float,
        default=0.40,
        help="Maximum bootstrap confidence-interval width for MAP.",
    )
    parser.add_argument(
        "--quality-gate-max-metric-uncertainty-patch-success-width",
        type=float,
        default=0.40,
        help="Maximum bootstrap confidence-interval width for patch success.",
    )
    parser.add_argument(
        "--quality-gate-min-metric-uncertainty-top1-lower",
        type=float,
        default=0.65,
        help="Minimum bootstrap lower confidence bound for Top-1.",
    )
    parser.add_argument(
        "--quality-gate-min-metric-uncertainty-map-lower",
        type=float,
        default=0.50,
        help="Minimum bootstrap lower confidence bound for MAP.",
    )
    parser.add_argument(
        "--quality-gate-min-metric-uncertainty-patch-success-lower",
        type=float,
        default=0.50,
        help="Minimum bootstrap lower confidence bound for patch success.",
    )
    parser.add_argument(
        "--quality-gate-min-localization-calibration-cases",
        type=int,
        default=1,
        help="Minimum cases in localization confidence calibration.",
    )
    parser.add_argument(
        "--quality-gate-max-localization-calibrated-ece",
        type=float,
        default=0.10,
        help="Maximum calibrated ECE in localization confidence calibration.",
    )
    parser.add_argument(
        "--quality-gate-min-localization-source-holdout-splits",
        type=int,
        default=1,
        help="Minimum source-group holdout splits in localization calibration.",
    )
    parser.add_argument(
        "--quality-gate-min-localization-holdout-train-cases",
        type=int,
        default=1,
        help="Minimum training cases per localization calibration holdout split.",
    )
    parser.add_argument(
        "--quality-gate-max-localization-holdout-calibrated-ece",
        type=float,
        default=0.10,
        help="Maximum holdout calibrated ECE in localization calibration.",
    )
    parser.add_argument(
        "--quality-gate-min-localization-attribution-coverage",
        type=float,
        default=0.95,
        help="Minimum FinalScore attribution coverage.",
    )
    parser.add_argument(
        "--quality-gate-max-localization-attribution-fragile-rate",
        type=float,
        default=0.90,
        help="Maximum fragile Top-1 rate in FinalScore attribution.",
    )
    parser.add_argument(
        "--quality-gate-max-localization-attribution-counterfactual-flip-rate",
        type=float,
        default=0.90,
        help="Maximum counterfactual Top-1 flip rate in FinalScore attribution.",
    )
    parser.add_argument(
        "--quality-gate-max-localization-attribution-reconstruction-error",
        type=float,
        default=0.50,
        help="Maximum average FinalScore attribution reconstruction error.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generation-selected-candidates-per-case",
        type=int,
        default=1,
        help=(
            "Minimum selected hard-case candidates per generated case when "
            "--run-quality-gate is enabled."
        ),
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generation-rule-coverage",
        type=int,
        default=3,
        help="Minimum generated hard-case rule coverage when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generation-function-coverage",
        type=int,
        default=1,
        help="Minimum generated hard-case function coverage when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generation-source-coverage",
        type=int,
        default=1,
        help="Minimum generated hard-case source coverage when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generation-candidate-score",
        type=float,
        default=0.0001,
        help="Minimum generated hard-case average candidate score when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generation-diversity-bonus",
        type=float,
        default=0.0,
        help="Minimum generated hard-case average diversity bonus when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generation-provenance-selected-ratio",
        type=float,
        default=0.80,
        help="Minimum provenance-selected generated hard-case ratio.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generation-provenance-bonus",
        type=float,
        default=0.50,
        help="Minimum generated hard-case average provenance bonus.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generation-provenance-source-sha-coverage",
        type=float,
        default=0.95,
        help="Minimum generated hard-case provenance source SHA coverage.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generation-provenance-stable-ref-coverage",
        type=float,
        default=0.95,
        help="Minimum generated hard-case provenance stable ref coverage.",
    )
    parser.add_argument(
        "--quality-gate-max-hard-case-generation-provenance-leakage-risk",
        type=float,
        default=0.30,
        help="Maximum generated hard-case provenance leakage risk.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-benchmark-cases",
        type=int,
        default=5,
        help="Minimum generated hard-case benchmark cases when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-patch-success-rate",
        type=float,
        default=0.50,
        help="Minimum generated hard-case benchmark patch success rate when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-multi-candidate-cases",
        type=int,
        default=1,
        help="Minimum generated hard-case multi-candidate cases when --run-quality-gate is enabled.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-score-inversions",
        type=int,
        default=2,
        help="Minimum generated hard-case score inversion cases when score-inversion probes are present.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-diversity-assisted-successes",
        type=int,
        default=1,
        help="Minimum generated hard-case diversity-assisted successes when diversity reranking probes are present.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-diversity-budget-sensitive-successes",
        type=int,
        default=1,
        help="Minimum generated hard-case successes pulled into budget by diversity reranking.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-success-diversity-lift",
        type=float,
        default=1.0,
        help="Minimum generated hard-case average success diversity lift when diversity reranking probes are present.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-success-diversity-bonus",
        type=float,
        default=0.0001,
        help="Minimum generated hard-case average success diversity bonus when diversity reranking probes are present.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-dedupe-affected-cases",
        type=int,
        default=1,
        help="Minimum generated hard-case dedupe-affected cases when deduplication probes are present.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-deduplicated-candidates",
        type=int,
        default=1,
        help="Minimum generated hard-case deduplicated candidates when deduplication probes are present.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-duplicate-pressure",
        type=float,
        default=0.0001,
        help="Minimum generated hard-case duplicate pressure when deduplication probes are present.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-reflection-success-cases",
        type=int,
        default=1,
        help="Minimum generated hard-case reflection-depth success cases when reflection probes are present.",
    )
    parser.add_argument(
        "--quality-gate-min-hard-case-generated-reflection-candidates",
        type=int,
        default=1,
        help="Minimum generated hard-case depth>0 reflection candidates when reflection probes are present.",
    )
    args = parser.parse_args()

    result = run_experiment_suite(
        template_path=Path(args.template),
        output_dir=Path(args.output_dir),
        patch_mode=args.patch_mode,
        judge_mode=args.judge_mode,
        patch_judge_mode=args.patch_judge_mode,
        llm_score_mode=args.llm_score_mode,
        use_dynamic_coverage=not args.no_dynamic_coverage,
        run_ablation=not args.skip_ablation,
        run_weight_search=not args.skip_weight_search,
        run_patch_weight_search=not args.skip_patch_weight_search,
        weight_search_top_n=args.top_n,
        source_cache_dir=Path(args.source_cache_dir)
        if args.source_cache_dir
        else None,
        run_quality_gate=args.run_quality_gate,
        run_showcase_report=args.run_showcase_report,
        sync_readme_showcase_path=Path(args.sync_readme_showcase)
        if args.sync_readme_showcase
        else None,
        hard_case_catalog_path=Path(args.hard_case_catalog)
        if args.hard_case_catalog
        else None,
        hard_case_max_cases_per_suggestion=(
            args.hard_case_max_cases_per_suggestion
        ),
        hard_case_max_total_cases=args.hard_case_max_total_cases,
        run_hard_case_generated_benchmark=(
            not args.skip_hard_case_generated_benchmark
        ),
        quality_gate_thresholds=QualityGateThresholds(
            min_cases=args.quality_gate_min_cases,
            min_top1=args.quality_gate_min_top1,
            min_top3=args.quality_gate_min_top3,
            min_patch_success_rate=args.quality_gate_min_patch_success_rate,
            min_slice_grounded_case_ratio=(
                args.quality_gate_min_slice_grounded_case_ratio
            ),
            min_average_top1_slice_support=(
                args.quality_gate_min_average_top1_slice_support
            ),
            min_average_top1_slice_failed_test_reachability=(
                args.quality_gate_min_average_top1_slice_failed_test_reachability
            ),
            min_average_top1_slice_call_chain_coverage=(
                args.quality_gate_min_average_top1_slice_call_chain_coverage
            ),
            min_weight_search_top1=args.quality_gate_min_weight_search_top1,
            min_weight_search_robust_score=(
                args.quality_gate_min_weight_search_robust_score
            ),
            min_weight_search_source_groups=(
                args.quality_gate_min_weight_search_source_groups
            ),
            max_weight_search_top1_gap=(
                args.quality_gate_max_weight_search_top1_gap
            ),
            max_weight_search_map_gap=args.quality_gate_max_weight_search_map_gap,
            min_llm_judge_cases=args.quality_gate_min_llm_judge_cases,
            max_llm_judge_brier_score=(
                args.quality_gate_max_llm_judge_brier_score
            ),
            max_llm_judge_ece=args.quality_gate_max_llm_judge_ece,
            min_llm_judge_agreement_rate=(
                args.quality_gate_min_llm_judge_agreement_rate
            ),
            min_patch_judge_candidates=(
                args.quality_gate_min_patch_judge_candidates
            ),
            max_patch_judge_brier_score=(
                args.quality_gate_max_patch_judge_brier_score
            ),
            max_patch_judge_ece=args.quality_gate_max_patch_judge_ece,
            min_patch_judge_agreement_rate=(
                args.quality_gate_min_patch_judge_agreement_rate
            ),
            max_patch_judge_fusion_validation_regression=(
                args.quality_gate_max_patch_judge_fusion_validation_regression
            ),
            max_patch_judge_fusion_top1_regression=(
                args.quality_gate_max_patch_judge_fusion_top1_regression
            ),
            max_patch_judge_fusion_mrr_regression=(
                args.quality_gate_max_patch_judge_fusion_mrr_regression
            ),
            max_patch_judge_fusion_success_margin_regression=(
                args.quality_gate_max_patch_judge_fusion_success_margin_regression
            ),
            max_patch_judge_fusion_first_success_rank_regression=(
                args.quality_gate_max_patch_judge_fusion_first_success_rank_regression
            ),
            min_difficulty_medium_cases=(
                args.quality_gate_min_difficulty_medium_cases
            ),
            min_difficulty_hard_cases=(
                args.quality_gate_min_difficulty_hard_cases
            ),
            min_difficulty_cross_file_patch_cases=(
                args.quality_gate_min_difficulty_cross_file_patch_cases
            ),
            min_difficulty_patch_competition_cases=(
                args.quality_gate_min_difficulty_patch_competition_cases
            ),
            min_difficulty_cross_function_data_flow_cases=(
                args.quality_gate_min_difficulty_cross_function_data_flow_cases
            ),
            min_bug_type_count=args.quality_gate_min_bug_type_count,
            min_expected_rule_count=args.quality_gate_min_expected_rule_count,
            min_cases_per_bug_type=args.quality_gate_min_cases_per_bug_type,
            min_cases_per_expected_rule=(
                args.quality_gate_min_cases_per_expected_rule
            ),
            min_generalization_source_groups=(
                args.quality_gate_min_generalization_source_groups
            ),
            min_generalization_holdout_cases=(
                args.quality_gate_min_generalization_holdout_cases
            ),
            min_generalization_balance_entropy=(
                args.quality_gate_min_generalization_balance_entropy
            ),
            max_generalization_top1_gap=(
                args.quality_gate_max_generalization_top1_gap
            ),
            max_generalization_map_gap=(
                args.quality_gate_max_generalization_map_gap
            ),
            max_generalization_patch_success_gap=(
                args.quality_gate_max_generalization_patch_success_gap
            ),
            max_generalization_search_efficiency_gap=(
                args.quality_gate_max_generalization_search_efficiency_gap
            ),
            max_generalization_worst_holdout_gap_score=(
                args.quality_gate_max_generalization_worst_holdout_gap_score
            ),
            min_generalization_stability_score=(
                args.quality_gate_min_generalization_stability_score
            ),
            min_benchmark_provenance_case_coverage=(
                args.quality_gate_min_benchmark_provenance_case_coverage
            ),
            min_benchmark_provenance_mutation_coverage=(
                args.quality_gate_min_benchmark_provenance_mutation_coverage
            ),
            min_benchmark_provenance_source_sha_coverage=(
                args.quality_gate_min_benchmark_provenance_source_sha_coverage
            ),
            min_benchmark_provenance_stable_ref_coverage=(
                args.quality_gate_min_benchmark_provenance_stable_ref_coverage
            ),
            min_benchmark_provenance_license_coverage=(
                args.quality_gate_min_benchmark_provenance_license_coverage
            ),
            max_benchmark_provenance_duplicate_signatures=(
                args.quality_gate_max_benchmark_provenance_duplicate_signatures
            ),
            max_benchmark_provenance_source_concentration=(
                args.quality_gate_max_benchmark_provenance_source_concentration
            ),
            max_benchmark_provenance_leakage_risk_score=(
                args.quality_gate_max_benchmark_provenance_leakage_risk_score
            ),
            min_search_budget_cases=args.quality_gate_min_search_budget_cases,
            min_search_budget_success_at_1=(
                args.quality_gate_min_search_budget_success_at_1
            ),
            min_search_budget_auc=args.quality_gate_min_search_budget_auc,
            max_search_budget_first_success_rank_p90=(
                args.quality_gate_max_search_budget_first_success_rank_p90
            ),
            min_search_budget_dedupe_affected_cases=(
                args.quality_gate_min_search_budget_dedupe_affected_cases
            ),
            min_search_budget_deduplicated_candidates=(
                args.quality_gate_min_search_budget_deduplicated_candidates
            ),
            min_search_budget_average_duplicate_pressure=(
                args.quality_gate_min_search_budget_average_duplicate_pressure
            ),
            min_search_competition_multi_candidate_cases=(
                args.quality_gate_min_search_competition_multi_candidate_cases
            ),
            min_search_competition_multi_candidate_rule_diversity=(
                args.quality_gate_min_search_competition_multi_candidate_rule_diversity
            ),
            min_search_competition_multi_candidate_failure_type_diversity=(
                args.quality_gate_min_search_competition_multi_candidate_failure_type_diversity
            ),
            min_search_competition_multi_candidate_retention_bucket_diversity=(
                args.quality_gate_min_search_competition_multi_candidate_retention_bucket_diversity
            ),
            min_search_competition_diversity_assisted_successes=(
                args.quality_gate_min_search_competition_diversity_assisted_successes
            ),
            min_search_competition_average_success_diversity_lift=(
                args.quality_gate_min_search_competition_average_success_diversity_lift
            ),
            min_search_competition_average_success_diversity_bonus=(
                args.quality_gate_min_search_competition_average_success_diversity_bonus
            ),
            min_metric_uncertainty_cases=(
                args.quality_gate_min_metric_uncertainty_cases
            ),
            max_metric_uncertainty_top1_width=(
                args.quality_gate_max_metric_uncertainty_top1_width
            ),
            max_metric_uncertainty_map_width=(
                args.quality_gate_max_metric_uncertainty_map_width
            ),
            max_metric_uncertainty_patch_success_width=(
                args.quality_gate_max_metric_uncertainty_patch_success_width
            ),
            min_metric_uncertainty_top1_lower=(
                args.quality_gate_min_metric_uncertainty_top1_lower
            ),
            min_metric_uncertainty_map_lower=(
                args.quality_gate_min_metric_uncertainty_map_lower
            ),
            min_metric_uncertainty_patch_success_lower=(
                args.quality_gate_min_metric_uncertainty_patch_success_lower
            ),
            min_localization_calibration_cases=(
                args.quality_gate_min_localization_calibration_cases
            ),
            max_localization_calibrated_ece=(
                args.quality_gate_max_localization_calibrated_ece
            ),
            min_localization_source_holdout_splits=(
                args.quality_gate_min_localization_source_holdout_splits
            ),
            min_localization_holdout_train_cases=(
                args.quality_gate_min_localization_holdout_train_cases
            ),
            max_localization_holdout_calibrated_ece=(
                args.quality_gate_max_localization_holdout_calibrated_ece
            ),
            min_localization_attribution_coverage=(
                args.quality_gate_min_localization_attribution_coverage
            ),
            max_localization_attribution_fragile_rate=(
                args.quality_gate_max_localization_attribution_fragile_rate
            ),
            max_localization_attribution_counterfactual_flip_rate=(
                args.quality_gate_max_localization_attribution_counterfactual_flip_rate
            ),
            max_localization_attribution_reconstruction_error=(
                args.quality_gate_max_localization_attribution_reconstruction_error
            ),
            min_hard_case_generation_selected_candidates_per_case=(
                args.quality_gate_min_hard_case_generation_selected_candidates_per_case
            ),
            min_hard_case_generation_rule_coverage=(
                args.quality_gate_min_hard_case_generation_rule_coverage
            ),
            min_hard_case_generation_function_coverage=(
                args.quality_gate_min_hard_case_generation_function_coverage
            ),
            min_hard_case_generation_source_coverage=(
                args.quality_gate_min_hard_case_generation_source_coverage
            ),
            min_hard_case_generation_candidate_score=(
                args.quality_gate_min_hard_case_generation_candidate_score
            ),
            min_hard_case_generation_diversity_bonus=(
                args.quality_gate_min_hard_case_generation_diversity_bonus
            ),
            min_hard_case_generation_provenance_selected_ratio=(
                args.quality_gate_min_hard_case_generation_provenance_selected_ratio
            ),
            min_hard_case_generation_provenance_bonus=(
                args.quality_gate_min_hard_case_generation_provenance_bonus
            ),
            min_hard_case_generation_provenance_source_sha_coverage=(
                args.quality_gate_min_hard_case_generation_provenance_source_sha_coverage
            ),
            min_hard_case_generation_provenance_stable_ref_coverage=(
                args.quality_gate_min_hard_case_generation_provenance_stable_ref_coverage
            ),
            max_hard_case_generation_provenance_leakage_risk=(
                args.quality_gate_max_hard_case_generation_provenance_leakage_risk
            ),
            min_hard_case_generated_benchmark_cases=(
                args.quality_gate_min_hard_case_generated_benchmark_cases
            ),
            min_hard_case_generated_patch_success_rate=(
                args.quality_gate_min_hard_case_generated_patch_success_rate
            ),
            min_hard_case_generated_multi_candidate_cases=(
                args.quality_gate_min_hard_case_generated_multi_candidate_cases
            ),
            min_hard_case_generated_score_inversions=(
                args.quality_gate_min_hard_case_generated_score_inversions
            ),
            min_hard_case_generated_diversity_assisted_successes=(
                args.quality_gate_min_hard_case_generated_diversity_assisted_successes
            ),
            min_hard_case_generated_diversity_budget_sensitive_successes=(
                args.quality_gate_min_hard_case_generated_diversity_budget_sensitive_successes
            ),
            min_hard_case_generated_success_diversity_lift=(
                args.quality_gate_min_hard_case_generated_success_diversity_lift
            ),
            min_hard_case_generated_success_diversity_bonus=(
                args.quality_gate_min_hard_case_generated_success_diversity_bonus
            ),
            min_hard_case_generated_dedupe_affected_cases=(
                args.quality_gate_min_hard_case_generated_dedupe_affected_cases
            ),
            min_hard_case_generated_deduplicated_candidates=(
                args.quality_gate_min_hard_case_generated_deduplicated_candidates
            ),
            min_hard_case_generated_duplicate_pressure=(
                args.quality_gate_min_hard_case_generated_duplicate_pressure
            ),
            min_hard_case_generated_reflection_success_cases=(
                args.quality_gate_min_hard_case_generated_reflection_success_cases
            ),
            min_hard_case_generated_reflection_candidates=(
                args.quality_gate_min_hard_case_generated_reflection_candidates
            ),
        )
        if args.run_quality_gate
        else None,
    )
    if args.format == "json":
        print(json.dumps(_json_ready(result), indent=2, ensure_ascii=False))
    else:
        print(result["markdown"])


def run_experiment_suite(
    template_path: Path,
    output_dir: Path,
    patch_mode: str = "rule",
    judge_mode: str = "none",
    patch_judge_mode: str = "none",
    llm_score_mode: str = "none",
    use_dynamic_coverage: bool = True,
    run_ablation: bool = True,
    run_weight_search: bool = True,
    run_patch_weight_search: bool = True,
    weight_search_top_n: int = 10,
    source_cache_dir: Path | None = None,
    run_quality_gate: bool = False,
    run_showcase_report: bool = False,
    sync_readme_showcase_path: Path | None = None,
    hard_case_catalog_path: Path | None = None,
    hard_case_max_cases_per_suggestion: int = 1,
    hard_case_max_total_cases: int | None = None,
    run_hard_case_generated_benchmark: bool = True,
    quality_gate_thresholds: QualityGateThresholds | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    materialized_dir = output_dir / "materialized"
    suite_json_path = output_dir / "suite.json"
    suite_markdown_path = output_dir / "suite.md"
    llm_config_audit = llm_config_audits_for_modes(
        patch_mode=patch_mode,
        judge_mode=judge_mode,
        patch_judge_mode=patch_judge_mode,
        llm_score_mode=llm_score_mode,
    )
    benchmark_result = run_template_benchmark(
        template_path=template_path,
        output_dir=materialized_dir,
        patch_mode=patch_mode,
        judge_mode=judge_mode,
        patch_judge_mode=patch_judge_mode,
        llm_score_mode=llm_score_mode,
        use_dynamic_coverage=use_dynamic_coverage,
        source_cache_dir=source_cache_dir,
    )
    manifest_path = Path(benchmark_result["manifest_path"])
    localizer = FaultLocalizer(
        llm_scorer=build_llm_fault_scorer(llm_score_mode)
    )
    ablation_results = []
    if run_ablation:
        ablation_results = BenchmarkAblationRunner(
            localizer=localizer,
            use_dynamic_coverage=use_dynamic_coverage,
        ).run_manifest(manifest_path)
    weight_results = []
    if run_weight_search:
        weight_results = WeightSearchRunner(
            localizer=localizer,
            use_dynamic_coverage=use_dynamic_coverage,
        ).search_manifest(manifest_path)[: max(1, weight_search_top_n)]
    patch_weight_results = []
    if run_patch_weight_search:
        patch_weight_results = PatchWeightSearchRunner(
            localizer=localizer,
            patch_judge=build_patch_judge(patch_judge_mode),
            use_dynamic_coverage=use_dynamic_coverage,
        ).search_manifest(manifest_path)[: max(1, weight_search_top_n)]

    result = {
        "template_path": str(template_path),
        "output_dir": str(output_dir),
        "materialized_dir": str(materialized_dir),
        "manifest_path": str(manifest_path),
        "settings": {
            "patch_mode": patch_mode,
            "judge_mode": judge_mode,
            "patch_judge_mode": patch_judge_mode,
            "llm_score_mode": llm_score_mode,
            "use_dynamic_coverage": use_dynamic_coverage,
            "run_ablation": run_ablation,
            "run_weight_search": run_weight_search,
            "run_patch_weight_search": run_patch_weight_search,
            "run_showcase_report": run_showcase_report,
            "sync_readme_showcase_path": (
                str(sync_readme_showcase_path)
                if sync_readme_showcase_path
                else ""
            ),
            "weight_search_top_n": max(1, weight_search_top_n),
            "source_cache_dir": str(source_cache_dir) if source_cache_dir else "",
            "hard_case_catalog_path": (
                str(hard_case_catalog_path) if hard_case_catalog_path else ""
            ),
            "hard_case_max_cases_per_suggestion": max(
                1,
                hard_case_max_cases_per_suggestion,
            ),
            "hard_case_max_total_cases": (
                hard_case_max_total_cases
                if hard_case_max_total_cases is not None
                else ""
            ),
            "run_hard_case_generated_benchmark": (
                run_hard_case_generated_benchmark
            ),
        },
        "llm_config_audit": llm_config_audit,
        "template_validation": benchmark_result["template_validation"],
        "manifest_validation": benchmark_result["manifest_validation"],
        "benchmark_report": benchmark_result["benchmark_report"],
        "ablation_results": ablation_results,
        "ablation_impact": ablation_impact_report(ablation_results)
        if ablation_results
        else None,
        "weight_search_results": weight_results,
        "patch_weight_search_results": patch_weight_results,
        "patch_judge_fusion_summary": patch_judge_fusion_summary(
            patch_weight_results
        ),
        "hard_case_mining": None,
        "benchmark_mining": None,
        "benchmark_mining_json_path": "",
        "benchmark_mining_markdown_path": "",
        "benchmark_mining_template_seeds_path": "",
        "hard_case_generation": None,
        "hard_case_generation_json_path": "",
        "hard_case_generation_markdown_path": "",
        "hard_case_generated_template_path": "",
        "hard_case_generated_benchmark": None,
        "hard_case_generated_benchmark_dir": "",
        "quality_gate": None,
        "showcase_report": None,
        "showcase_report_json_path": "",
        "showcase_report_markdown_path": "",
        "resume_showcase_markdown_path": "",
        "readme_showcase_sync_path": "",
        "readme_showcase_sync_changed": False,
        "readme_showcase_sync_initial_mismatch_count": 0,
        "readme_showcase_sync_mismatch_count": 0,
        "markdown": "",
    }
    result["hard_case_mining"] = hard_case_mining_report(_json_ready(result))
    benchmark_mining_report = mine_benchmark_template_seeds(
        _json_ready(result),
        source_path=str(suite_json_path),
    )
    result["benchmark_mining"] = benchmark_mining_report
    benchmark_mining_json_path = output_dir / "benchmark_mining.json"
    benchmark_mining_markdown_path = output_dir / "benchmark_mining.md"
    result["benchmark_mining_json_path"] = str(benchmark_mining_json_path)
    result["benchmark_mining_markdown_path"] = str(benchmark_mining_markdown_path)
    benchmark_mining_json_path.write_text(
        json.dumps(benchmark_mining_report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    benchmark_mining_markdown_path.write_text(
        render_benchmark_mining_markdown(benchmark_mining_report),
        encoding="utf-8",
    )
    if benchmark_mining_report.template_seeds:
        template_seeds_path = output_dir / "benchmark_mining_template_seeds.json"
        result["benchmark_mining_template_seeds_path"] = str(template_seeds_path)
        template_seeds_path.write_text(
            json.dumps(
                {
                    "cases": [
                        item.template_case
                        for item in benchmark_mining_report.template_seeds
                    ]
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    if hard_case_catalog_path is not None:
        catalog_payload = json.loads(hard_case_catalog_path.read_text(encoding="utf-8"))
        generation_report = generate_hard_case_candidates(
            _json_ready(result),
            catalog_payload,
            suite_path=str(output_dir / "suite.json"),
            catalog_path=str(hard_case_catalog_path),
            max_cases_per_suggestion=max(1, hard_case_max_cases_per_suggestion),
            max_total_cases=hard_case_max_total_cases,
        )
        result["hard_case_generation"] = generation_report
        generation_json_path = output_dir / "hard_case_generation.json"
        generation_markdown_path = output_dir / "hard_case_generation.md"
        result["hard_case_generation_json_path"] = str(generation_json_path)
        result["hard_case_generation_markdown_path"] = str(generation_markdown_path)
        generation_json_path.write_text(
            json.dumps(generation_report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        generation_markdown_path.write_text(
            render_hard_case_generation_markdown(generation_report),
            encoding="utf-8",
        )
        if generation_report.generated_count > 0:
            generated_template_path = output_dir / "hard_case_generated_template.json"
            result["hard_case_generated_template_path"] = str(generated_template_path)
            generated_template_path.write_text(
                json.dumps(
                    generation_report.template,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            if run_hard_case_generated_benchmark:
                generated_benchmark_dir = output_dir / "hard_case_generated_benchmark"
                result["hard_case_generated_benchmark_dir"] = str(
                    generated_benchmark_dir
                )
                result["hard_case_generated_benchmark"] = run_template_benchmark(
                    template_path=generated_template_path,
                    output_dir=generated_benchmark_dir,
                    patch_mode=patch_mode,
                    judge_mode=judge_mode,
                    patch_judge_mode=patch_judge_mode,
                    llm_score_mode=llm_score_mode,
                    use_dynamic_coverage=use_dynamic_coverage,
                    source_cache_dir=source_cache_dir,
                )
    if run_quality_gate:
        result["quality_gate"] = evaluate_quality_gate(
            _json_ready(result),
            thresholds=quality_gate_thresholds,
        )
    result["suite_json_path"] = str(suite_json_path)
    result["suite_markdown_path"] = str(suite_markdown_path)
    if run_showcase_report or sync_readme_showcase_path:
        showcase_report = build_showcase_report(_json_ready(result))
        showcase_json_path = output_dir / "showcase_report.json"
        showcase_markdown_path = output_dir / "showcase_report.md"
        resume_showcase_path = output_dir / "resume_showcase.md"
        result["showcase_report"] = showcase_report
        result["showcase_report_json_path"] = str(showcase_json_path)
        result["showcase_report_markdown_path"] = str(showcase_markdown_path)
        result["resume_showcase_markdown_path"] = str(resume_showcase_path)
        showcase_json_path.write_text(
            json.dumps(showcase_report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        showcase_markdown_path.write_text(
            render_showcase_markdown(showcase_report),
            encoding="utf-8",
        )
        resume_showcase_path.write_text(
            render_resume_showcase_markdown(showcase_report),
            encoding="utf-8",
        )
        if sync_readme_showcase_path:
            readme_text = sync_readme_showcase_path.read_text(encoding="utf-8")
            expected_metrics = showcase_overview_metrics(showcase_report)
            initial_mismatches = readme_showcase_mismatches(
                readme_text,
                expected_metrics,
            )
            updated_readme = sync_readme_showcase_text(
                readme_text,
                expected_metrics,
            )
            changed = updated_readme != readme_text
            if changed:
                sync_readme_showcase_path.write_text(
                    updated_readme,
                    encoding="utf-8",
                )
            final_mismatches = readme_showcase_mismatches(
                updated_readme,
                expected_metrics,
            )
            result["readme_showcase_sync_path"] = str(sync_readme_showcase_path)
            result["readme_showcase_sync_changed"] = changed
            result["readme_showcase_sync_initial_mismatch_count"] = len(
                initial_mismatches
            )
            result["readme_showcase_sync_mismatch_count"] = len(final_mismatches)
    markdown = render_experiment_suite_markdown(result)
    result["markdown"] = markdown
    suite_json_path.write_text(
        json.dumps(_json_ready(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    suite_markdown_path.write_text(markdown, encoding="utf-8")
    return result


def render_experiment_suite_markdown(result: dict) -> str:
    benchmark_report = result["benchmark_report"]
    ablation_results = result["ablation_results"]
    weight_results = result["weight_search_results"]
    patch_weight_results = result["patch_weight_search_results"]
    hard_case_mining = result.get("hard_case_mining")
    benchmark_mining = result.get("benchmark_mining")
    hard_case_generation = result.get("hard_case_generation")
    hard_case_generated_benchmark = result.get("hard_case_generated_benchmark")
    quality_gate_result = result.get("quality_gate")
    showcase_report = result.get("showcase_report")
    settings = result["settings"]
    lines = [
        "# Experiment Suite",
        "",
        f"- Template: `{result['template_path']}`",
        f"- Manifest: `{result['manifest_path']}`",
        f"- Dynamic Coverage: `{settings['use_dynamic_coverage']}`",
        f"- Patch Mode: `{settings['patch_mode']}`",
        f"- Judge Mode: `{settings['judge_mode']}`",
        f"- Patch Judge Mode: `{settings['patch_judge_mode']}`",
        f"- LLMScore Mode: `{settings['llm_score_mode']}`",
        f"- Source Cache: `{settings['source_cache_dir'] or 'default'}`",
        "",
        render_llm_config_audit_markdown(result.get("llm_config_audit", {})),
        "",
        "## Benchmark",
        "",
        render_benchmark_markdown(benchmark_report),
    ]
    if ablation_results:
        lines.extend(
            [
                "",
                "## Ablation Study",
                "",
                render_ablation_markdown(ablation_results),
            ]
        )
    if weight_results:
        lines.extend(
            [
                "",
                "## FinalScore Weight Search",
                "",
                render_weight_search_markdown(
                    weight_results,
                    top_n=settings["weight_search_top_n"],
                ),
            ]
        )
    if patch_weight_results:
        lines.extend(
            [
                "",
                "## PatchScore Weight Search",
                "",
                render_patch_weight_search_markdown(
                    patch_weight_results,
                    top_n=settings["weight_search_top_n"],
                ),
            ]
        )
    if hard_case_mining:
        lines.extend(["", render_hard_case_mining_markdown(hard_case_mining)])
    if benchmark_mining:
        lines.extend(["", render_benchmark_mining_markdown(benchmark_mining)])
    if hard_case_generation:
        lines.extend(["", render_hard_case_generation_markdown(hard_case_generation)])
    if hard_case_generated_benchmark:
        lines.extend(
            [
                "",
                "## Generated Hard-Case Benchmark",
                "",
                f"- Manifest: `{hard_case_generated_benchmark['manifest_path']}`",
                "",
                render_benchmark_markdown(
                    hard_case_generated_benchmark["benchmark_report"]
                ),
            ]
        )
    if quality_gate_result:
        lines.extend(["", render_quality_gate_markdown(quality_gate_result)])
    if showcase_report:
        lines.extend(["", render_showcase_markdown(showcase_report)])
    return "\n".join(lines)


def _json_ready(result: dict) -> dict:
    payload = {
        "template_path": result["template_path"],
        "output_dir": result["output_dir"],
        "materialized_dir": result["materialized_dir"],
        "manifest_path": result["manifest_path"],
        "settings": result["settings"],
        "llm_config_audit": result.get("llm_config_audit", {}),
        "template_validation": result["template_validation"],
        "manifest_validation": result["manifest_validation"],
        "benchmark_report": result["benchmark_report"].to_dict(),
        "ablation_results": [
            asdict(item) for item in result["ablation_results"]
        ],
        "ablation_impact": (
            result["ablation_impact"].to_dict()
            if result.get("ablation_impact")
            else None
        ),
        "weight_search_results": [
            item.to_dict() for item in result["weight_search_results"]
        ],
        "patch_weight_search_results": [
            item.to_dict() for item in result["patch_weight_search_results"]
        ],
        "patch_judge_fusion_summary": (
            result["patch_judge_fusion_summary"].to_dict()
            if result.get("patch_judge_fusion_summary")
            else None
        ),
        "hard_case_mining": (
            result["hard_case_mining"].to_dict()
            if result.get("hard_case_mining")
            else None
        ),
        "benchmark_mining": (
            result["benchmark_mining"].to_dict()
            if result.get("benchmark_mining")
            else None
        ),
        "benchmark_mining_json_path": result.get(
            "benchmark_mining_json_path",
            "",
        ),
        "benchmark_mining_markdown_path": result.get(
            "benchmark_mining_markdown_path",
            "",
        ),
        "benchmark_mining_template_seeds_path": result.get(
            "benchmark_mining_template_seeds_path",
            "",
        ),
        "hard_case_generation": (
            result["hard_case_generation"].to_dict()
            if result.get("hard_case_generation")
            else None
        ),
        "hard_case_generation_json_path": result.get(
            "hard_case_generation_json_path",
            "",
        ),
        "hard_case_generation_markdown_path": result.get(
            "hard_case_generation_markdown_path",
            "",
        ),
        "hard_case_generated_template_path": result.get(
            "hard_case_generated_template_path",
            "",
        ),
        "hard_case_generated_benchmark": _benchmark_result_json_ready(
            result.get("hard_case_generated_benchmark")
        ),
        "hard_case_generated_benchmark_dir": result.get(
            "hard_case_generated_benchmark_dir",
            "",
        ),
        "quality_gate": (
            result["quality_gate"].to_dict()
            if result.get("quality_gate")
            else None
        ),
        "showcase_report": result.get("showcase_report"),
        "showcase_report_json_path": result.get("showcase_report_json_path", ""),
        "showcase_report_markdown_path": result.get(
            "showcase_report_markdown_path",
            "",
        ),
        "resume_showcase_markdown_path": result.get(
            "resume_showcase_markdown_path",
            "",
        ),
        "readme_showcase_sync_path": result.get(
            "readme_showcase_sync_path",
            "",
        ),
        "readme_showcase_sync_changed": result.get(
            "readme_showcase_sync_changed",
            False,
        ),
        "readme_showcase_sync_initial_mismatch_count": result.get(
            "readme_showcase_sync_initial_mismatch_count",
            0,
        ),
        "readme_showcase_sync_mismatch_count": result.get(
            "readme_showcase_sync_mismatch_count",
            0,
        ),
        "markdown": result["markdown"],
    }
    if "suite_json_path" in result:
        payload["suite_json_path"] = result["suite_json_path"]
    if "suite_markdown_path" in result:
        payload["suite_markdown_path"] = result["suite_markdown_path"]
    return payload


def _benchmark_result_json_ready(result: dict | None) -> dict | None:
    if not result:
        return None
    return {
        "template_validation": result["template_validation"],
        "manifest_path": result["manifest_path"],
        "manifest_validation": result["manifest_validation"],
        "report_artifacts": result["report_artifacts"],
        "benchmark_report": result["benchmark_report"].to_dict(),
    }


if __name__ == "__main__":
    main()
