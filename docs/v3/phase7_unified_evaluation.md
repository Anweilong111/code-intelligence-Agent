# V3 Unified Evaluation and Release Readiness

- Status: `partial`
- Reason: `offline_release_evidence_passed_live_trials_pending`
- Offline release: `pass`
- Complete release: `pending`
- Claim eligible: false

## Release Gates

| Gate | Result |
| --- | --- |
| `all_phase_evidence_parseable` | pass |
| `phase0_protocol_frozen` | pass |
| `phase1_real_bug_benchmark_passed` | pass |
| `phase2_repository_startup_passed` | pass |
| `phase3_offline_foundation_passed` | pass |
| `phase4_localization_passed` | pass |
| `phase5_semantic_validation_passed` | pass |
| `phase6_memory_and_security_passed` | pass |
| `live_provider_access_preflight_passed` | pending/fail |
| `live_llm_hybrid_120_trials_complete` | pending/fail |
| `rule_llm_hybrid_attribution_available` | pending/fail |
| `live_cost_latency_and_failure_taxonomy_available` | pending/fail |
| `full_v3_claim_eligible` | pending/fail |

## Phase Evidence

| Phase | Source status | Gate | Artifact | SHA-256 |
| --- | --- | --- | --- | --- |
| `phase0` | `pass` | `pass` | `docs/v3/phase0_verification.json` | `26c93a701d6a` |
| `phase1` | `pass` | `pass` | `docs/v3/phase1_verification.json` | `0fcd8fffc4ef` |
| `phase2` | `pass` | `pass` | `docs/v3/phase2_verification.json` | `b87e5b3171d4` |
| `phase3` | `partial` | `pass` | `docs/v3/phase3_offline_verification.json` | `adbb5e227d99` |
| `phase4` | `pass` | `pass` | `docs/v3/phase4_verification.json` | `da47701e940b` |
| `phase5` | `pass` | `pass` | `docs/v3/phase5_verification.json` | `ef3a726cc3f1` |
| `phase6` | `pass` | `pass` | `docs/v3/phase6_verification.json` | `23a2613bac8f` |

## Metric Registry

| Dimension | Evidence state | Headline | Source |
| --- | --- | --- | --- |
| `benchmark` | `measured` | 20 accepted real bugs from 6 repositories | `docs/v3/phase1_verification.json` |
| `repository_environment` | `measured` | 19/20 test processes started and terminated | `docs/v3/phase2_verification.json` |
| `fault_localization` | `measured` | frozen test Top-1/3/5=0.60/0.80/1.00 | `docs/v3/phase4_verification.json` |
| `planner` | `safety_policy_measured` | conflict and repository-injection overrides are rejected | `docs/v3/phase6_verification.json` |
| `reflection` | `pending` | requires 120 live LLM/Hybrid trials | `pending_live_evaluation` |
| `semantic_validation` | `calibration_only` | 2/2 human fixes accepted | `docs/v3/phase5_verification.json` |
| `memory` | `measured` | completion 0.4286 -> 1.0000 | `docs/v3/phase6_verification.json` |
| `security` | `measured_controlled_fixtures` | 8/8 controlled threats handled | `docs/v3/phase6_verification.json` |
| `cost_and_latency` | `partial` | Rule cost USD 0; live LLM/Hybrid cost pending | `docs/v3/phase3_offline_verification.json and live evaluation` |

## Repair Strategies

| Strategy | State | pass@1 | pass@3 | Verified | Reflection | Cost USD | Latency ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `rule` | `measured` | 0.0000 | 0.0000 | 0.0000 | n/a | 0.0000 | n/a |
| `llm` | `pending` | pending | pending | pending | pending | pending | pending |
| `hybrid` | `pending` | pending | pending | pending | pending | pending | pending |

## Provider Access Preflight

- Status: `pending`
- Counted as repair trial: `false`
- Cost USD: `pending`
- Latency ms: `pending`

## Proportion Uncertainty

| Metric | Observed | Wilson 95% interval |
| --- | ---: | --- |
| Repository startup | 19/20 | [0.7639, 0.9911] |
| Localization Top-1 | 3/5 | [0.2307, 0.8824] |
| Localization Top-3 | 4/5 | [0.3755, 0.9638] |
| Localization Top-5 | 5/5 | [0.5655, 1.0000] |
| Rule pass@1 | 0/20 | [0.0000, 0.1611] |

## Protocol Comparisons

| Comparison | Status | Reason |
| --- | --- | --- |
| `v2_v3_repository_startup` | `not_comparable` | V3 adds isolated runtimes and new startup policy; raw counts are context only |
| `v2_v3_fault_localization` | `not_comparable` | V3 uses a real-bug repository-disjoint split and V2 uses a controlled mutation benchmark |
| `v2_v3_patch_repair` | `not_comparable` | V2 LLM metrics use deterministic fixtures; V3 live-model metrics are pending |
| `v2_v3_memory` | `not_comparable` | V3 changes authority, conflict, stale-scope, and advisory-memory gates |

## Pending Requirements

- `environment_injected_provider_access`: Use an environment-injected provider key with valid authentication, billing/quota, and frozen-model access; never persist the key.
- `provider_access_preflight`: Record one passing frozen provider-access preflight with exact-model verification and separate token, cost, and latency overhead.
- `llm_trials`: Run 20 real bugs x 3 independent LLM trials (60 trials).
- `hybrid_trials`: Run 20 real bugs x 3 independent Hybrid trials (60 trials).
- `complete_live_artifact`: Supply a passing evaluation with 120/120 trials, zero missing trials, RunRecord audit pass, pass@1/pass@3, semantic verification, reflection, token, cost, latency, failure taxonomy, generator attribution, and a passing provider-access preflight.

## Claim Boundaries

- Offline Phase 0-6 evidence does not substitute for the pending 120 live LLM and Hybrid trials.
- Rule metrics, human-fix semantic calibration, and deterministic memory/security fixtures are not live-model repair rates.
- V2/V3 numbers are not presented as improvements when their protocols differ.
- A complete V3 release requires all failed trials, provider blockers, environment blockers, token usage, cost, latency, and generator attribution in the denominator.
- Provider-access preflight overhead is reported separately and never counted as a repair Trial or pass@k success.
- Process-level repository defenses do not provide container-grade isolation for native child processes on Windows.
