# V3 Unified Evaluation and Release Readiness

- Status: `pass`
- Reason: `all_v3_release_gates_passed`
- Offline release: `pass`
- Complete release: `pass`
- Claim eligible: true

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
| `live_provider_access_preflight_passed` | pass |
| `live_llm_hybrid_120_trials_complete` | pass |
| `rule_llm_hybrid_attribution_available` | pass |
| `live_cost_latency_and_failure_taxonomy_available` | pass |
| `full_v3_claim_eligible` | pass |

## Phase Evidence

| Phase | Source status | Gate | Artifact | SHA-256 |
| --- | --- | --- | --- | --- |
| `phase0` | `pass` | `pass` | `docs/v3/phase0_verification.json` | `26c93a701d6a` |
| `phase1` | `pass` | `pass` | `docs/v3/phase1_verification.json` | `0fcd8fffc4ef` |
| `phase2` | `pass` | `pass` | `docs/v3/phase2_verification.json` | `b87e5b3171d4` |
| `phase3` | `partial` | `pass` | `docs/v3/phase3_offline_verification.json` | `adbb5e227d99` |
| `phase4` | `pass` | `pass` | `docs/v3/phase4_verification.json` | `da47701e940b` |
| `phase5` | `pass` | `pass` | `docs/v3/phase5_verification.json` | `6ae38cad8167` |
| `phase6` | `pass` | `pass` | `docs/v3/phase6_verification.json` | `23a2613bac8f` |

## Metric Registry

| Dimension | Evidence state | Headline | Source |
| --- | --- | --- | --- |
| `benchmark` | `measured` | 20 accepted real bugs from 6 repositories | `docs/v3/phase1_verification.json` |
| `repository_environment` | `measured` | 19/20 test processes started and terminated | `docs/v3/phase2_verification.json` |
| `fault_localization` | `measured` | frozen test Top-1/3/5=0.60/0.80/1.00 | `docs/v3/phase4_verification.json` |
| `planner` | `safety_policy_measured` | conflict and repository-injection overrides are rejected | `docs/v3/phase6_verification.json` |
| `reflection` | `measured` | live reflection recovery available | `outputs_v3/phase3_live_20260717_334eee/evaluation.json` |
| `semantic_validation` | `calibration_only` | 2/2 human fixes accepted | `docs/v3/phase5_verification.json` |
| `memory` | `measured` | completion 0.4286 -> 1.0000 | `docs/v3/phase6_verification.json` |
| `security` | `measured_controlled_fixtures` | 8/8 controlled threats handled | `docs/v3/phase6_verification.json` |
| `cost_and_latency` | `measured` | Rule, LLM, and Hybrid cost/latency available | `docs/v3/phase3_offline_verification.json and live evaluation` |

## Repair Strategies

| Strategy | State | pass@1 | pass@3 | Verified | Reflection | Cost USD | Latency ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `rule` | `measured` | 0.0000 | 0.0000 | 0.0000 | n/a | 0.0000 | n/a |
| `llm` | `measured` | 0.4000 | 0.5000 | 0.5000 | 0.3500 | 1.8396 | 15739038.0000 |
| `hybrid` | `measured` | 0.3000 | 0.4500 | 0.4500 | 0.1500 | 1.0069 | 15563464.0000 |

## Provider Access Preflight

- Status: `pass`
- Counted as repair trial: `false`
- Cost USD: `0.0000`
- Latency ms: `1072.0000`

## RunRecord Evidence

- Status: `pass`
- Records: `423`
- SHA-256: `574fa23283105a7f75f0953e50de0cf53a5d7e6909f914438d206d07f530cdb8`
- Raw RunRecords copied into release report: `false`

## Trial Cost and Latency Distribution

| Strategy | Trials | Cost mean | Cost stddev | Latency mean ms | Latency stddev ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| `llm` | 60 | 0.0307 | 0.0172 | 262317.3000 | 188708.5080 |
| `hybrid` | 60 | 0.0168 | 0.0139 | 259391.0670 | 191862.8160 |

## Proportion Uncertainty

| Metric | Observed | Wilson 95% interval |
| --- | ---: | --- |
| Repository startup | 19/20 | [0.7639, 0.9911] |
| Localization Top-1 | 3/5 | [0.2307, 0.8824] |
| Localization Top-3 | 4/5 | [0.3755, 0.9638] |
| Localization Top-5 | 5/5 | [0.5655, 1.0000] |
| Rule pass@1 | 0/20 | [0.0000, 0.1611] |
| LLM pass@1 | 8/20 | [0.2188, 0.6134] |
| LLM pass@3 | 10/20 | [0.2993, 0.7007] |
| Hybrid pass@1 | 6/20 | [0.1455, 0.5190] |
| Hybrid pass@3 | 9/20 | [0.2582, 0.6579] |

## Protocol Comparisons

| Comparison | Status | Reason |
| --- | --- | --- |
| `v2_v3_repository_startup` | `not_comparable` | V3 adds isolated runtimes and new startup policy; raw counts are context only |
| `v2_v3_fault_localization` | `not_comparable` | V3 uses a real-bug repository-disjoint split and V2 uses a controlled mutation benchmark |
| `v2_v3_patch_repair` | `not_comparable` | V2 LLM metrics use deterministic fixtures while V3 uses a completed 120-trial live-model real-bug protocol; no uplift is calculated |
| `v2_v3_memory` | `not_comparable` | V3 changes authority, conflict, stale-scope, and advisory-memory gates |

## Generator Attribution

| Strategy | Winning generator families | Provider blockers | Environment blockers |
| --- | --- | ---: | ---: |
| `rule` | none | 0 | 0 |
| `llm` | llm:25 | 0 | 0 |
| `hybrid` | llm:22 | 1 | 0 |

## Audited Live Examples

| Type | Status | Case | Strategy/trial | Generator | Outcome or failure |
| --- | --- | --- | --- | --- | --- |
| Direct repair | `measured` | `bugsinpy-fastapi-4` | `llm/1` | `llm_direct` | `verified_repair` |
| Reflection repair | `measured` | `bugsinpy-black-10` | `hybrid/2` | `llm_reflection` | `verified_repair` |
| Failed repair | `measured` | `bugsinpy-black-2` | `llm/1` | `llm_direct` | `targeted_test:test_assertion_failure` |
| Provider blocker | `measured` | `bugsinpy-black-2` | `hybrid/2` | `llm_direct` | `provider:timeout` |

- Environment blockers: `1`
- Controlled security cases: `8`

## Pending Requirements

- none

## Claim Boundaries

- Offline Phase 0-6 evidence is combined with a validated 120-trial live LLM/Hybrid evaluation; neither substitutes for the other.
- Rule metrics, human-fix semantic calibration, and deterministic memory/security fixtures are not live-model repair rates.
- V2/V3 numbers are not presented as improvements when their protocols differ.
- A complete V3 release retains all failed trials, provider blockers, environment blockers, token usage, cost, latency, and generator attribution in the denominator.
- Provider-access preflight overhead is reported separately and never counted as a repair Trial or pass@k success.
- Process-level repository defenses do not provide container-grade isolation for native child processes on Windows.
