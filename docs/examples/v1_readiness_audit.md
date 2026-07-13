# V1 Readiness Audit

This page summarizes the tracked v1 target-dataset audit for the arbitrary
Python GitHub repository Agent. It is a compact GitHub-facing snapshot of the
dataset and metric contract, not a replacement for running the full suite.

## Reproduce

```bash
python -m code_intelligence_agent.evaluation.v1_readiness_dataset_audit ^
  datasets/github_cases/repo_intelligence_v1_onboarding_30.example.json ^
  datasets/github_cases/llm_repair_case_catalog_v1_50.example.json ^
  outputs/v1_readiness_dataset_audit ^
  --format markdown --require-pass
```

## Audit Result

| Area | Check | Result |
| --- | --- | ---: |
| Onboarding | Public GitHub repository cases | 30/30 |
| Onboarding | Unique repository inputs | 30/30 |
| Onboarding | Required scenario coverage | 13/13 |
| Repair/evaluation | Total repair/evaluation cases | 50/50 |
| Repair/evaluation | Repair class kinds | 3/3 |
| Repair/evaluation | Blocker category kinds | 8/8 |
| Metrics | Required metric contracts | 9/9 |
| Metrics | Incomplete metric contracts | 0 |

Status: `pass`

Agent loop: `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`

## Required Metrics

| Metric | Main evidence |
| --- | --- |
| `onboarding_success_rate` | `github_repo_intelligence_suite.json`, `github_onboarding_matrix.json` |
| `topk_localization_accuracy` | `fault_localization.json`, `benchmark_report.json`, `phase4_search_evaluation.json` |
| `pass_at_1` | `repository_test_patch_validation.json`, `llm_repair_metrics_report.json` |
| `pass_at_k` | `repository_test_patch_validation.json`, `llm_repair_metrics_report.json` |
| `reflection_uplift` | `reflection_trace.json`, `repository_test_patch_validation.json`, `llm_repair_metrics_report.json` |
| `blocker_accuracy` | `github_repo_agent_controller.json`, `github_repo_intelligence.json`, `llm_repair_case_catalog_audit.json` |
| `sandbox_success_rate` | `repository_test_patch_validation.json`, `llm_repair_metrics_report.json` |
| `average_runtime_ms` | `github_repo_intelligence_suite.runs.elapsed_ms`, `suite_run_elapsed_ms_average` |
| `llm_cost_usd` | `repository_test_patch_candidates.json`, `repository_llm_patch_estimated_cost_usd_total` |

## What This Proves

The target dataset is ready to support the v1 evaluation story:

- 30 public GitHub onboarding cases for arbitrary-repo analysis coverage.
- 50 repair/evaluation cases covering direct repair, reflection repair, and
  blocker cases.
- Explicit metric contracts for localization, repair, reflection, blockers,
  sandbox validation, runtime, and LLM cost.
- A readable audit artifact that can fail fast when the dataset no longer
  matches the declared v1 target.

## Metric Summary

After the readiness audit, use the V1 evaluation summary to merge available
runtime evidence. The onboarding input can be a full suite report or a slice
aggregate report:

```bash
python -m code_intelligence_agent.evaluation.v1_evaluation_summary ^
  outputs/v1_evaluation_summary ^
  --readiness-audit outputs/v1_readiness_dataset_audit/v1_readiness_dataset_audit.json ^
  --onboarding-suite outputs/v1_onboarding_aggregate/v1_onboarding_slice_aggregate.json ^
  --repair-metrics outputs/v1_repair/llm_repair_metrics_report.json ^
  --repair-catalog-audit outputs/v1_repair/llm_repair_case_catalog_audit.json ^
  --localization-report outputs/v1_repair/phase4_search_evaluation.json ^
  --llm-cost-report outputs/v1_cost/llm_cost_evidence.json
```

The summary reports each required metric as `measured`, `proxy`, or
`missing_evidence`. That distinction is important: the Agent should not invent
Top-k localization, reflection uplift, runtime, or cost numbers when the
corresponding evidence artifact has not been generated.

For long onboarding runs, the suite runner also supports resumable slices:

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite ^
  datasets/github_cases/repo_intelligence_v1_onboarding_30.example.json ^
  outputs/v1_onboarding_slice_0_5 ^
  --start-index 0 ^
  --limit-runs 5
```

Aggregate one or more slice reports before passing onboarding evidence to the
metric summary:

```bash
python -m code_intelligence_agent.evaluation.v1_onboarding_slice_aggregate ^
  datasets/github_cases/repo_intelligence_v1_onboarding_30.example.json ^
  outputs/v1_onboarding_aggregate ^
  outputs/v1_onboarding_slice_0_5_suite.json
```

Slice aggregates are useful for progress checks and debugging slow
repositories. They report completed, failed, and missing manifest rows, then
print the next `--start-index` / `--limit-runs` command. They are treated as
proxy evidence until all 30 onboarding rows have been generated and audited.

When provider usage tokens are present but cost was not computed during the LLM
request, generate standalone cost evidence with configured pricing:

```bash
python -m code_intelligence_agent.evaluation.llm_cost_evidence ^
  outputs/v1_cost ^
  outputs/v1_repair/repository_test_patch_candidates.json ^
  --input-usd-per-1k-tokens 0.001 ^
  --output-usd-per-1k-tokens 0.002 ^
  --require-pass
```

The pricing values are explicit inputs to the audit. They should match the
provider billing model used for the run before the cost is presented as an
actual provider estimate.

## Boundary

This audit proves the target dataset and metric contract are complete. It does
not by itself prove that every source report has been freshly regenerated.
Before claiming final v1 completion, run the onboarding suite and repair suites
that feed these catalogs.
