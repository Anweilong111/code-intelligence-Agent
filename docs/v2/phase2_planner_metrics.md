# Phase 2 LLM Planner and AgentController

## Result

Phase 2 passes its controlled planning, safety, and regression gates. The LLM
can now propose and, when safe, change the next Agent action. The rules remain
the execution authority for registration, arguments, transitions, risk,
confirmation, budgets, repeated-state protection, and sandbox verification.

The Planner receives the user goal, repository profile, current stage, Top-k
localization, static and graph evidence, pytest/traceback evidence, blocker,
executed actions, failed patch fingerprints, user constraints, memory, and
remaining action/time/cost budgets. A valid response must include all of the
following fields:

`selected_action`, `arguments`, `reason`, `confidence`, `risk`,
`required_evidence`, `expected_outcome`, `fallback_action`,
`termination_condition`, `memory_used`, and `next_plan`.

## Strategy Comparison

The offline suite contains 14 deterministic scenarios and executes each with
Rule, LLM, and Hybrid planning for 42 total runs.

| Planner | Completion | Invalid Actions | Avg Actions | Blocker Accuracy | Avg Runtime (ms) | Tokens | Cost (USD) | Safety Rejects | Fallbacks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Rule | 1.0000 | 0 | 1.0000 | 1.0000 | 0.5030 | 0 | 0.00000000 | 0 | 0 |
| LLM | 1.0000 | 2 | 1.0000 | 1.0000 | 1.0013 | 1560 | 0.01560000 | 8 | 11 |
| Hybrid | 1.0000 | 2 | 1.0000 | 1.0000 | 0.9884 | 1560 | 0.01560000 | 9 | 12 |

The two invalid LLM actions are intentional fixtures: an unregistered action
and an action with non-allowlisted arguments. Both are rejected before action
materialization. Fallback totals also include low confidence, high risk,
invalid fallback, exhausted budget, repeated unchanged observation, unsafe
transition, provider failure, invalid JSON, and incomplete schema cases.

Hybrid is intentionally more conservative when the model and rule planner
disagree: LLM mode requires confidence `>= 0.65`, while Hybrid requires
confidence `>= 0.75` for a different action. This distinction is covered by a
moderate-confidence disagreement case.

## Safety Boundary

- The model can select only an Action Registry entry.
- Arguments are normalized through an action-specific allowlist; shell text is
  not an accepted planner argument.
- A different action must stay within an allowed phase/tool/module transition
  and have an implemented automatic executor.
- Registry risk overrides model-claimed risk. High-risk alternatives require
  confirmation and are not automatically adopted.
- Action, elapsed-time, and estimated LLM-cost budgets can stop execution.
- An exhausted budget is checked before provider invocation, so a blocked
  planning turn consumes no additional model request. Intermediate report
  construction uses the Rule controller and reuses the current Planner
  snapshot, preventing duplicate model calls for one observation.
- Each auto action is followed by a new Observe step. A stable observation
  fingerprint prevents repeating the same action in an unchanged failure
  state without blocking a retry after new evidence arrives.
- Provider and schema failures are classified and use the rule planner rather
  than terminating the complete Agent run.
- The LLM Planner cannot declare a repair successful. Patch safety gates and
  sandbox test evidence remain authoritative.

## Regression

The frozen V1 baseline contains 1127 passing tests. The Phase 2 full suite
contains 1163 passing tests, zero failures, and completed in 602.53 seconds on
the recorded development environment.

## Reproduction

```powershell
python -m code_intelligence_agent.evaluation.planner_strategy_evaluation `
  datasets/planner_evaluation/v2_planner_controlled_cases.json `
  outputs/phase2_planner_eval `
  --format markdown `
  --require-pass
```

```powershell
python -m pytest -q `
  tests/test_agent_controller.py `
  tests/test_planner_strategy_evaluation.py `
  tests/test_github_repo_intelligence.py
```

```powershell
python -m pytest -q
```

The machine-readable evidence is in
`docs/v2/phase2_planner_metrics.json`.

## Interpretation Limits

The controlled completion value means the expected safe planning outcome was
selected. It is not a claim that all repository repairs succeed. The LLM
responses are deterministic offline fixtures, so token and cost values verify
accounting and comparison logic rather than live-provider quality or pricing.
Real repair success must still be supported by patch artifacts and sandbox
test results.
