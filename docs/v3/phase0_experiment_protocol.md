# V3 Phase 0 Experiment Protocol

- Status: `pass`
- Protocol SHA-256: `4780c0ab09f747cd63bb8bc979f3d20a798d42e9bd1206d16c20abd444454035`
- Baseline: `v2-baseline` -> `cf571489ac35c4dfcff44d7def0c9310b8206b2b`
- Baseline tag verified: `True`
- Provider/model: `deepseek/deepseek-v4-pro`
- Temperature: `0`
- Rule trials per case: `1`
- LLM trials per case: `3`
- Hybrid trials per case: `3`
- Pricing snapshot: `deepseek-v4-pro-2026-07-14`
- Cache-hit input price: `$0.003625` per million tokens
- Cache-miss input price: `$0.435` per million tokens
- Output price: `$0.87` per million tokens
- Protocol errors: `0`

## Frozen Prompts

| Prompt | SHA-256 |
| --- | --- |
| judge_v3 | `6cca02f6af7d2f1f348ac98b3b5d7be23fe35b90c209f8be310e869beef2ca88` |
| localization_v3 | `1ed1c43aa72804e37b8d2788a953fde6bc96cdf7d1d77c361beecbeb1e4a2b86` |
| patch_generation_v3 | `c5898a77322fc5aa1394cc538c97bde51bda31affcde0f0610eba80231259c47` |
| planner_v3 | `c6d2ca2b7a0f26c7bad2b28ddefad06017ab12a6a0ab9f258685f141bcf5d771` |
| provider_access_preflight_v3 | `08b9678b3cbf41ef34edfe8480e34ab9fe37b32a070c3dc1837f7f85c37cd5fa` |
| reflection_v3 | `0778d16ec733db013a30567626b0de50524de5c7398530f932d2a8428bd0faa2` |

## Attribution Rules

- Rule candidates have zero model tokens and zero model cost.
- LLM and Hybrid trials use independent trial identifiers; provider retries do not create trials.
- Hybrid success is credited to the winning candidate's generator family.
- Provider and environment blockers are excluded from application repair failures and reported separately.
- A verified repair requires AST validity, safety pass, targeted tests, full regression, and applicable semantic validation.

## Sources

- [https://api-docs.deepseek.com/quick_start/pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [https://api-docs.deepseek.com/api/list-models](https://api-docs.deepseek.com/api/list-models)

## Boundary

This artifact freezes the evaluation contract. It does not call a model and does not report a live repair rate.
