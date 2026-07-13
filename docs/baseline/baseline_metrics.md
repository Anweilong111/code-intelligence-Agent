# Code Intelligence Agent V1 Baseline

- Status: `pass`
- Baseline Ref: `v1-baseline`
- Agent Loop: `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`
- Evidence Checks: 6/6 pass
- Full Test Suite: 1127 passed, 0 failed in 615.77s

## Dataset And Evaluation Scope

| Item | Value |
| --- | ---: |
| Public GitHub onboarding cases | 30 |
| Repair/evaluation cases | 50 |
| Required metric contracts | 9 |
| Directly measured metrics | 9 |

## Baseline Metrics

| Metric | Evidence Status | Value | Note |
| --- | --- | ---: | --- |
| `onboarding_success_rate` | `measured` | 1.0 | computed from suite passed runs over total runs |
| `topk_localization_accuracy` | `measured` | 1.0 | computed from benchmark localization top3 |
| `pass_at_1` | `measured` | 0.0 | computed from first successful sandbox rank at k=1 |
| `pass_at_k` | `measured` | 0.0 | computed from first successful sandbox rank at k=5 |
| `reflection_uplift` | `measured` | 0.1333 | computed as cases recovered by reflection over all repair cases |
| `blocker_accuracy` | `measured` | 1.0 | computed from expected vs observed blocker category |
| `sandbox_success_rate` | `measured` | 0.6957 | computed from sandbox-passed candidates over executed candidates |
| `average_runtime_ms` | `measured` | 4130.7 | computed from suite run elapsed time |
| `llm_cost_usd` | `measured` | 0.007966 | computed from standalone LLM token and pricing evidence |

## Evidence Gates

| Gate | Status | Evidence |
| --- | --- | --- |
| `dataset_readiness` | `pass` | onboarding=30; repair=50; required_metrics=9 |
| `evaluation_metrics` | `pass` | measured=9; required=9; proxy=0; missing=0 |
| `v1_goal_audit` | `pass` | passed=7; checks=7 |
| `full_test_suite` | `pass` | passed=1127; failed=0; duration_seconds=615.77 |
| `release_hygiene` | `pass` | passed=5; checks=5 |
| `agent_loop_contract` | `pass` | Observe -> Plan -> Act -> Verify -> Reflect -> Replan |

## Capability Boundaries

- The baseline targets public Python GitHub repositories, not every language.
- Repository analysis is general; verified repair still requires executable test evidence.
- Dependency, network, credential, timeout, and configuration failures are reported as blockers.
- LLM judge scores are advisory; sandbox pytest remains the repair authority.
- A clean repository may correctly produce no patch candidate.

## Reproduce

```powershell
python -m code_intelligence_agent.evaluation.v1_baseline outputs/v1_baseline --run-tests --require-pass
```
