# V1 Evaluation Summary

This page records the current v1 evidence snapshot for the arbitrary Python
GitHub repository Agent. It combines the 30-repository onboarding aggregate,
the repair/evaluation metrics report, the blocker catalog audit, and the
benchmark localization report.

## Evidence Snapshot

| Area | Result |
| --- | ---: |
| Readiness audit | pass |
| Onboarding repositories completed | 30/30 |
| Onboarding failed runs | 0 |
| Required metric contracts | 9/9 |
| Directly measured metrics | 9/9 |
| Proxy metrics | 0 |
| Missing evidence metrics | 0 |

Agent loop: `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`

## Metrics

| Metric | Status | Value | Evidence |
| --- | --- | ---: | --- |
| `onboarding_success_rate` | `measured` | 1.0 | 30-repository onboarding aggregate |
| `topk_localization_accuracy` | `measured` | 1.0 | benchmark `summary.top3` |
| `pass_at_1` | `measured` | 0.0 | repair metrics `patch_success_at.1` |
| `pass_at_k` | `measured` | 0.0 | repair metrics `patch_success_at.5` |
| `reflection_uplift` | `measured` | 0.1333 | repair metrics `reflection_success_case_rate` |
| `blocker_accuracy` | `measured` | 1.0 | blocker catalog expected/observed matches |
| `sandbox_success_rate` | `measured` | 0.6957 | sandbox-passed candidates over executed candidates |
| `average_runtime_ms` | `measured` | 4130.7 | onboarding aggregate elapsed time |
| `llm_cost_usd` | `measured` | 0.007966 | standalone token and configured pricing evidence |

## Boundary

The evidence supports the v1 claim that the Agent can run an auditable
arbitrary-repository onboarding pipeline across 30 public Python GitHub
repositories and connect that result to localization, repair, blocker,
reflection, sandbox, and runtime metrics.

The LLM cost value is an estimate computed from provider token usage and an
explicitly configured pricing model. It is not a stored API key and should not
be treated as a real-time provider bill unless the configured rates match the
provider billing terms used for that run.
