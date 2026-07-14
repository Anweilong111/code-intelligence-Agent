# Phase 7 Budget Ablation Evaluation

- Status: `pass`
- Reason: `all_budget_ablation_expectations_met`
- Patch success authority: targeted pytest plus full regression pytest.
- Action harness: production controller and Action Registry with controlled tool outcomes.

## Reflection

| Value | Candidates | AST Valid | Safety Pass | Target Pass | Regression Safe | Verified | Reflection | Runtime ms |
| ---: | ---: | ---: | ---: | --- | --- | --- | --- | ---: |
| 0 | 1 | 1.0000 | 1.0000 | false | false | false | false | 530.1712 |
| 1 | 1 | 1.0000 | 1.0000 | true | true | true | true | 3534.4359 |

## Candidate Budget

| Value | Candidates | AST Valid | Safety Pass | Target Pass | Regression Safe | Verified | Reflection | Runtime ms |
| ---: | ---: | ---: | ---: | --- | --- | --- | --- | ---: |
| 1 | 1 | 1.0000 | 1.0000 | false | false | false | false | 476.3803 |
| 2 | 2 | 1.0000 | 1.0000 | false | false | false | false | 930.0522 |
| 3 | 3 | 1.0000 | 1.0000 | true | true | true | false | 3962.4329 |

## Top K Context

| Value | Candidates | AST Valid | Safety Pass | Target Pass | Regression Safe | Verified | Reflection | Runtime ms |
| ---: | ---: | ---: | ---: | --- | --- | --- | --- | ---: |
| 1 | 0 | 0.0000 | 0.0000 | false | false | false | false | 17.4947 |
| 3 | 1 | 1.0000 | 1.0000 | true | true | true | false | 2650.8750 |
| 5 | 1 | 1.0000 | 1.0000 | true | true | true | false | 3011.1816 |

## Action Budget

| Budget | Completed | Actions | Valid Action Rate | Repeated Rate | Stop Reason | Runtime ms |
| ---: | --- | ---: | ---: | ---: | --- | ---: |
| 1 | false | 1 | 1.0000 | 0.0000 | action_budget_exhausted | 0.8284 |
| 2 | false | 2 | 1.0000 | 0.0000 | action_budget_exhausted | 1.4161 |
| 3 | true | 3 | 1.0000 | 0.0000 | task_completed | 2.3217 |

## Acceptance Gates

- `reflection_changes_outcome`: pass
- `candidate_budget_changes_outcome`: pass
- `top_k_context_changes_outcome`: pass
- `action_budget_changes_outcome`: pass
- `all_controller_actions_registered`: pass
- `no_repeated_controller_action`: pass

## Limitations

- Patch outcomes use deterministic offline LLM responses and real pytest sandbox validation.
- Action-budget runs use the production controller and Action Registry with deterministic state-transition tool outcomes.
- The controlled action harness measures budget sensitivity; it is not a GitHub repair-success estimate.
