# Phase 7 System Evaluation and Ablation

- Status: `pass`
- Reason: `all_phase7_evaluation_contracts_met`
- Evaluation commit: `a47e37b564b7f9271faac981e4b6437e2fcbd17f`
- Working tree clean: `true`

## Required Comparisons

| Comparison | Complete | Raw Artifact |
| --- | --- | --- |
| Rule Patch vs LLM Patch vs Hybrid Patch | pass | [patch_strategy](patch_strategy_evaluation.json) |
| Rule Planner vs LLM Planner vs Hybrid Planner | pass | [planner_strategy](planner_strategy_evaluation.json) |
| With Graph vs Without Graph | pass | [graph](localization_split_evaluation.json) |
| With Dynamic Evidence vs Without Dynamic Evidence | pass | [dynamic_evidence](localization_split_evaluation.json) |
| With Memory vs Without Memory | pass | [memory](memory_ablation_evaluation.json) |
| With Reflection vs Without Reflection | pass | [reflection](budget_ablation_evaluation.json) |
| Top-k Context Sizes | pass | [top_k_context](budget_ablation_evaluation.json) |
| Action Budget and Candidate Budget | pass | [action_and_candidate_budget](budget_ablation_evaluation.json) |

## Localization Metrics

| Cases | Top-1 | Top-3 | Top-5 | MRR | MAP | Mean Latency ms |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 19.0052 |

## Localization Signal Ablations

| Profile | Top-1 | Top-3 | Top-5 | MRR | MAP | Mean Latency ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fusion | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 19.0052 |
| without_graph | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 19.0052 |
| without_dynamic | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 19.0052 |

## Patch Strategies

| Mode | Candidate Success | AST Valid | Safety Pass | Test Pass | Regression Safe | Verified | Reflection Recovery | Runtime ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rule | 0.3333 | 1.0000 | 1.0000 | 0.3333 | 0.3333 | 0.3333 | 0.0000 | 1280.2533 |
| llm | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.3333 | 3327.4613 |
| hybrid | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.3333 | 3574.8704 |

## Planner Strategies

| Mode | Completion | Valid Action | Invalid Proposals | Blocker Accuracy | Avg Actions | Runtime ms | Tokens | Cost USD | Repeated Action |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rule | 1.0000 | 1.0000 | 0 | 1.0000 | 1.0000 | 0.7791 | 0 | 0.00000000 | 0.0000 |
| llm | 1.0000 | 1.0000 | 2 | 1.0000 | 1.0000 | 1.6716 | 1560 | 0.01560000 | 0.0000 |
| hybrid | 1.0000 | 1.0000 | 2 | 1.0000 | 1.0000 | 1.6754 | 1560 | 0.01560000 | 0.0000 |

## Memory Ablation

| Mode | Completion | Recall | Constraint Preservation | Failed Patch Avoidance | Repeated Failed Patch | Prompt Chars | Runtime ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| without_memory | 0.1250 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 2.0000 | 0.0159 |
| with_memory | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 710.1250 | 0.2277 |

## Patch Budget Ablations

| Dimension | Value | Candidates | Target Pass | Regression Safe | Verified | Reflection Recovery | Runtime ms |
| --- | ---: | ---: | --- | --- | --- | --- | ---: |
| reflection | 0 | 1 | false | false | false | false | 530.1712 |
| reflection | 1 | 1 | true | true | true | true | 3534.4359 |
| candidate_budget | 1 | 1 | false | false | false | false | 476.3803 |
| candidate_budget | 2 | 2 | false | false | false | false | 930.0522 |
| candidate_budget | 3 | 3 | true | true | true | false | 3962.4329 |
| top_k_context | 1 | 0 | false | false | false | false | 17.4947 |
| top_k_context | 3 | 1 | true | true | true | false | 2650.8750 |
| top_k_context | 5 | 1 | true | true | true | false | 3011.1816 |

## Action Budget Ablation

| Budget | Completed | Actions | Valid Action | Repeated Action | Stop Reason | Runtime ms |
| ---: | --- | ---: | ---: | ---: | --- | ---: |
| 1 | false | 1 | 1.0000 | 0.0000 | action_budget_exhausted | 0.8284 |
| 2 | false | 2 | 1.0000 | 0.0000 | action_budget_exhausted | 1.4161 |
| 3 | true | 3 | 1.0000 | 0.0000 | task_completed | 2.3217 |

## V1 vs V2 Comparable Metrics

| Scope | Metric | V1 | V2 | Delta |
| --- | --- | ---: | ---: | ---: |
| localization_validation | top1 | 1.0000 | 1.0000 | 0.0000 |
| localization_validation | top3 | 1.0000 | 1.0000 | 0.0000 |
| localization_validation | top5 | 1.0000 | 1.0000 | 0.0000 |
| localization_validation | mrr | 1.0000 | 1.0000 | 0.0000 |
| localization_validation | map | 1.0000 | 1.0000 | 0.0000 |
| localization_test | top1 | 1.0000 | 1.0000 | 0.0000 |
| localization_test | top3 | 1.0000 | 1.0000 | 0.0000 |
| localization_test | top5 | 1.0000 | 1.0000 | 0.0000 |
| localization_test | mrr | 1.0000 | 1.0000 | 0.0000 |
| localization_test | map | 1.0000 | 1.0000 | 0.0000 |
| localization_blind | top1 | 1.0000 | 1.0000 | 0.0000 |
| localization_blind | top3 | 1.0000 | 1.0000 | 0.0000 |
| localization_blind | top5 | 1.0000 | 1.0000 | 0.0000 |
| localization_blind | mrr | 1.0000 | 1.0000 | 0.0000 |
| localization_blind | map | 1.0000 | 1.0000 | 0.0000 |

## Conclusions

- Removing GraphScore changed Top-1 by 0.0000 on this rule-detectable mutation benchmark; no graph uplift is claimed.
- Removing SBFL, TestFailureScore, and StackTraceScore changed Top-1 by 0.0000; the current benchmark does not demonstrate dynamic-evidence uplift.
- Structured memory changed controlled task completion from 0.1250 to 1.0000 and repeated failed-patch rate from 1.0000 to 0.0000.
- Controlled verified-repair rates were rule=0.3333, LLM=1.0000, and hybrid=1.0000; these deterministic fixtures validate orchestration, not live-model quality.
- All planner modes selected a valid registered action in the controlled suite; LLM and hybrid each consumed 1560 fixture tokens while rule planning consumed none.
- In the controlled sensitivity cases, the first verified candidate budget was 3, the first successful Top-k context was 3, and the first completing action budget was 3.
- V2 preserved V1 Top-1/Top-3/Top-5/MRR/MAP on all comparable localization splits; incompatible repair protocols are not reported as uplift.

## Failure Accounting

- `planner_expectation_failures`: 0
- `memory_task_failures`: 7
- `patch_unverified_runs`: 2
- `budget_expected_non_success_runs`: 6
- `phase6_partial_repositories`: 13
- `phase6_failed_test_processes`: 6
- `policy`: Failures remain in raw artifacts and are included in denominators.

## Claim Boundaries

- Controlled LLM fixtures measure orchestration, schema, safety, attribution, reflection, and budget behavior; they do not estimate live-model quality.
- Localization uses mutation cases with rule-detectable faults; equal fusion and rule-only scores do not prove fusion superiority.
- Phase 6 success means static analysis completed and an authorized test process started and terminated; it does not mean tests passed or a repair succeeded.
- Patch success is counted only when targeted and full regression pytest pass; LLM Judge is never success authority.
- V1 and V2 repair metrics with different datasets are marked non-comparable rather than reported as uplift.

## Raw Artifacts

- planner: [JSON](planner_strategy_evaluation.json), [Markdown](planner_strategy_evaluation.md)
- memory: [JSON](memory_ablation_evaluation.json), [Markdown](memory_ablation_evaluation.md)
- localization: [JSON](localization_split_evaluation.json), [Markdown](localization_split_evaluation.md)
- patch: [JSON](patch_strategy_evaluation.json), [Markdown](patch_strategy_evaluation.md)
- budgets: [JSON](budget_ablation_evaluation.json), [Markdown](budget_ablation_evaluation.md)
- system: [JSON](phase7_system_metrics.json), [Markdown](phase7_system_evaluation.md)
