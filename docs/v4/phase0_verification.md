# V4 Phase 0 Experiment Protocol Audit

- Status: `pass`
- Protocol SHA-256: `3375dda3486f617262a5ec68245ffc5e8f1d7dc37e4f4308f963935fb361395b`
- V3 baseline: `v3-baseline` -> `43268748cbfb4abb1f54c2e8d41da96e5ba1d92a`
- Baseline tag verified: `True`
- Provider/model: `deepseek/deepseek-v4-pro`
- Benchmark target: `50` cases / `15` repositories
- Split policy: `repository_disjoint`
- Protocol errors: `0`

## Equal-Budget Experiments

| Experiment | Cases | Allocation axis | Allocations | Trials |
| --- | ---: | --- | --- | ---: |
| primary_agent_effectiveness | 50 | policy_variant | fixed_workflow, full_agent | 3 |
| component_ablation | 20 | policy_variant | fixed_workflow, rule_planner, llm_planner, no_reflection, no_memory, full_agent | 3 |
| routed_hybrid | 50 | patch_strategy | llm_only, naive_hybrid, routed_hybrid | 3 |

## Frozen Prompts

| Prompt | SHA-256 |
| --- | --- |
| agent_policy_v4 | `d9e94a9c1a22fb41779d663d39da5c31c8c2b3ddb630d5496a8cb72e6e534f3f` |
| localization_v4 | `aea59ff513006603ba841ac88ebfe0ec1c764fcbe269233e23451aaa41fe5430` |
| patch_generation_v4 | `671c5b05b70e6b9abe4ee1172f0a3151234d32f3bbb0a873244c90122da9c9f9` |
| provider_access_preflight_v4 | `730d18f4a3b546e8bc27048a8489bd04e10b875263c11fabcb85f953a1eb7a52` |
| reflection_v4 | `cae3478a849d89511fc14b5cd69182ef61c9d28ef16b21683ce03e4b3dc4537e` |
| router_v4 | `818186ad8749ab1daa245c02543a4a299be331bfe0c505eb50172ad13a8c5f81` |
| semantic_risk_v4 | `27f5c7a05523d0c547743cc89952632e0330fdb5513d35039ebc580446baecce` |

## Claim Boundary

This artifact freezes the V4 comparison contract. It does not call a model, lock the future 50-case manifest, or claim an Agent improvement.
The Full Agent claim is allowed only after repository-disjoint, equal-budget, complete-denominator trials pass RunRecord audit.
