# V3 Phase 7 Unified Evaluation and Release Protocol

## Objective

Phase 7 converts the separate V3 benchmark, environment, repair, localization,
semantic, memory, and security artifacts into one release decision. The
aggregator is intentionally stricter than a dashboard: it can report offline
readiness while refusing a complete release claim when live evidence is absent,
incomplete, stale, or structurally invalid.

The current committed result is `partial`. Phase 0-6 offline evidence passes,
but the final frozen-protocol provider preflight returns HTTP 402 before any
repository preparation or repair Trial. The required 60 LLM and 60 Hybrid
trials therefore remain unsubmitted.

## Evidence States

Every reported dimension uses one of these states:

| State | Meaning |
| --- | --- |
| `measured` | The value is computed from the named V3 artifact and denominator. |
| `calibration_only` | Human-fix validator evidence; not an Agent repair result. |
| `safety_policy_measured` | A controller security property is tested, but no task-success metric is claimed. |
| `measured_controlled_fixtures` | A deterministic regression suite passed; arbitrary-world coverage is not claimed. |
| `partial` | Some components are measured and required live components remain absent. |
| `pending` | No numeric value is emitted until the required artifact exists. |
| `not_comparable` | V2 and V3 protocols differ, so no uplift is calculated. |

Missing LLM/Hybrid values are serialized as JSON `null` and rendered as
`pending`. They are never replaced by zero, Rule results, V2 fixture results, or
semantic calibration results.

## Offline Evidence Gates

The aggregator reads and SHA-256 fingerprints these committed artifacts:

1. `phase0_verification.json`: frozen baseline, protocol, Prompt hashes, model,
   pricing, randomness, and RunRecord contract.
2. `phase1_verification.json`: 20 accepted real Python bugs and independent
   bug/fix reproduction requirements.
3. `phase2_verification.json`: isolated repository startup and blocker
   classification.
4. `phase3_offline_verification.json`: preparation privacy/scope audit and Rule
   baseline. `status=partial` is allowed only when
   `offline_foundation_status=pass`.
5. `phase4_verification.json`: repository-disjoint difficult localization.
6. `phase5_verification.json`: semantic correctness gate calibration.
7. `phase6_verification.json`: memory authority and hostile-repository defense.

If any artifact is missing, invalid JSON, or fails its phase-specific gate,
`offline_release_status=fail` and the unified result is `fail`.

## Live Evaluation Acceptance

A supplied live evaluation is accepted only when all conditions hold:

- top-level `status=pass` and `live_model=true`;
- one passing provider-access preflight whose provider, exact response model,
  frozen system Prompt hash, frozen request Prompt hash, nonnegative usage,
  cost, and latency all pass audit;
- the preflight retains no response content, is attributed only to provider
  overhead, and is not counted as a repair Trial;
- exactly 20 accepted cases;
- `record_audit.status=pass`;
- LLM and Hybrid are both present;
- completeness is exactly 120 expected, 120 observed, and 0 missing trials;
- each strategy has 20 case denominators and 60 observed independent trials;
- each strategy includes pass@1, pass@3, verified repair, reflection recovery,
  AST validity, safety, targeted tests, full regression, semantic claim
  eligibility, cost, latency, failure categories, and winning generator family;
- token usage is present for both strategies;
- at least 120 model RunRecords have complete provider, model ID, Prompt ID, and
  Prompt template hash metadata;
- the protocol provider, exact model ID, and frozen Prompt hash map are present.

An artifact that says `pass` but contains 119 trials remains `invalid`. Provider
retries do not create independent trial identities because completeness is
derived from case, strategy, and trial index.

## Provider and Prompt Audit

`v3_repair_evaluation` aggregates model metadata already stored in validated
RunRecords:

- frozen provider and exact model ID;
- temperature, thinking mode, and reasoning effort;
- frozen Prompt ID-to-SHA-256 map;
- observed provider and model IDs;
- provider-returned model IDs;
- model call dates;
- unique request/system Prompt hash counts and aggregate set hashes.

Raw Prompts, raw provider payloads, private reasoning, and API keys are not
persisted in the release report.

The preflight is audited separately from repair RunRecords. Its system Prompt
file and runtime request Prompt are independently frozen by SHA-256; model drift
or Prompt drift blocks execution before repository preparation.

## Statistics and Denominators

The unified report keeps all failed trials and separates provider/environment
blockers from source-repair failures. Missing trials are never imputed.

For binomial proportions currently backed by committed evidence, the report
adds 95% Wilson intervals:

```text
p = successes / n
center = (p + z^2/(2n)) / (1 + z^2/n)
margin = z * sqrt(p(1-p)/n + z^2/(4n^2)) / (1 + z^2/n)
z = 1.96
```

This exposes sample-size uncertainty. For example, Top-5 `5/5` is reported with
interval `[0.5655, 1.0000]`, not treated as proof of universal 100% localization.

## Comparison Policy

V2/V3 differences are calculated only when the protocols match. The current
paired startup set has raw counts of V2 `7/20` and V3 `19/20`, but V3 adds
isolated runtimes and a changed startup policy, so the report labels the pair
`not_comparable` and does not claim a `+60%` causal improvement.

Localization, patch, and memory comparisons are also withheld because their
datasets or semantics changed. V2 deterministic LLM fixtures can demonstrate
orchestration but cannot be compared with pending V3 live-model repair rates.

## Current Offline Result

Measured evidence includes:

- benchmark: 20 accepted real bugs from 6 repositories;
- environment: 19/20 repository test processes started and terminated;
- localization: frozen test Top-1/3/5 = 0.60/0.80/1.00;
- Rule repair: pass@1/pass@3/verified repair = 0/0/0 on 20 cases;
- semantic calibration: 2/2 human fixes accepted, not Agent repairs;
- memory: controlled completion 0.4286 -> 1.0000 with zero stale,
  conflicting, or advisory execution;
- security: 8/8 hostile fixtures rejected, isolated, or accurately reported;
- latest full regression: 1408 passed and 2 explicit Windows symlink skips in
  755.54 seconds from 1410 collected tests.

LLM/Hybrid repair, Reflection recovery, provider token usage, live cost, live
latency, direct success examples, and Reflection success examples remain
pending.

## Commands

Offline pre-release evaluation:

```powershell
python -m code_intelligence_agent v3-release-eval `
  outputs_v3/phase7_release `
  --root . `
  --require-offline-pass
```

Complete evaluation after the live artifact exists:

```powershell
python -m code_intelligence_agent v3-release-eval `
  outputs_v3/phase7_release `
  --root . `
  --live-evaluation outputs_v3/phase3_live/evaluation.json `
  --require-complete
```

`--require-complete` exits nonzero while the result is `partial` or `fail`.

## Completion Boundary

The committed Phase 7 report is an offline release-readiness artifact, not the
final V3 result. Phase 7 can be marked complete only after the valid 120-trial
artifact is supplied, the unified report transitions to `pass`, complete
regression and release hygiene pass again, and every public metric links to its
source artifact.
