# V3 Phase 0 Verification

## Result

- Status: `pass`
- V2 baseline tag: `v2-baseline`
- V2 baseline commit: `cf571489ac35c4dfcff44d7def0c9310b8206b2b`
- Protocol SHA-256: `4780c0ab09f747cd63bb8bc979f3d20a798d42e9bd1206d16c20abd444454035`
- Frozen prompts: `6`
- RunRecord schema: `3.0`

## Verification

| Check | Result |
| --- | --- |
| Protocol audit | pass, 0 errors |
| Focused regression | 22 passed in 2.13s |
| Complete regression | 1244 passed in 883.25s |
| Release hygiene | 5/5 checks pass |
| Secret scan | 0 raw key findings |

## Protocol Amendment

On 2026-07-16, a real DeepSeek batch returned HTTP 402 after the paid smoke
case had completed. The first amendment classified this as
`billing_or_quota` and stopped new work after already-running workers finished.
The second amendment adds one frozen provider-access preflight before any
repository preparation or repair worker is submitted. It uses at most 16
output tokens, disables reasoning, requires an HTTP 200 chat-completion
envelope and the exact frozen response model, and retains only hashes and safe
metadata rather than response content. Both its system Prompt file and runtime
request Prompt are pinned by SHA-256.

The preflight is operational overhead, not a repair Trial. Its tokens, cost,
and latency are recorded separately and it cannot increase pass@1, pass@3, or
the 120-Trial denominator. A failed preflight stops all case preparation and
Trial submission; a Trial-level blocker still requires `--retry-blockers` when
resuming an older blocked attempt.

This amendment changes the protocol SHA and invalidates historical trial resume
fingerprints. It does not weaken any repair, safety, or completeness gate.

The first complete regression run found one stale V1 test fixture: its temporary `.gitignore` did not include the newly required `outputs_v3/` entry. That run completed with 1243 passing tests and one fixture failure. The fixture was updated, its focused tests passed, and the full suite was rerun to the all-green result above. No product execution behavior was changed by that correction.

## Frozen Contract

- Rule runs exactly once per case; LLM and Hybrid run three independent trials per case.
- Provider retries remain inside the same trial and cannot inflate pass@k.
- Hybrid success is attributed to the generator family of the verified winning candidate.
- Rule candidates have zero model tokens and zero model cost.
- Prompt ID and SHA-256 must match the frozen protocol for every LLM candidate.
- Provider and environment blockers are reported separately from application repair failures.
- Verified repair requires AST validity, safety pass, targeted tests, full regression, and applicable semantic validation.
- API keys are environment-only, and model response bodies are forbidden from RunRecord artifacts.

## Boundary

Phase 0 freezes the real-bug experiment contract. It does not call a live model and does not report a real repair rate. Those measurements begin after the reproducible real-bug benchmark is built.
