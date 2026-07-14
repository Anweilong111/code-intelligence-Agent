# V3 Phase 0 Verification

## Result

- Status: `pass`
- V2 baseline tag: `v2-baseline`
- V2 baseline commit: `cf571489ac35c4dfcff44d7def0c9310b8206b2b`
- Protocol SHA-256: `38822eb4caa146106182717bb5847a725d4277a9ce9ba73e6b8c0f4108972445`
- Frozen prompts: `5`
- RunRecord schema: `3.0`

## Verification

| Check | Result |
| --- | --- |
| Protocol audit | pass, 0 errors |
| Focused regression | 17 passed in 1.79s |
| Complete regression | 1244 passed in 883.25s |
| Release hygiene | 5/5 checks pass |
| Secret scan | 0 raw key findings |

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
