# Phase 8 Release Verification

- Status: `pass`
- Reason: `all_phase8_release_and_definition_of_done_checks_met`
- Tested implementation commit: `39ab6607f942bbd47b29293d55fd2e5c13fcc736`
- Generated: `2026-07-14T23:19:27+08:00`

## Verification Summary

| Check | Result | Evidence |
| --- | --- | --- |
| Full pytest regression | pass | `1234 passed in 787.62s` |
| Clean source snapshot | pass | `git archive` contained no `.git` or local `outputs*`; `25 passed in 2.73s` |
| Release hygiene | pass | 5/5 checks; no raw keys, tracked outputs or binary documents |
| Phase 6 unfamiliar repositories | pass | 20/20 structured reports and static analysis; 7/20 test processes started and terminated |
| Phase 7 comparisons | pass | 8/8 required patch/planner/graph/dynamic/memory/reflection/Top-k/budget comparisons |
| README and public docs | pass | Commands, links, evidence boundaries and V2 metrics checked by tests |

The first full Phase 8 regression was not hidden: it finished with `1233 passed, 1 failed in 701.30s`. The failure was a temporary V1 baseline test fixture whose synthetic `.gitignore` did not contain the newly required `outputs_v2/` and `outputs_demo/` patterns. Commit `39ab660` synchronized that fixture; focused verification then produced `16 passed`, and the complete rerun produced `1234 passed`.

## Definition of Done

| ID | Requirement | Status | Primary evidence |
| ---: | --- | --- | --- |
| 1 | New public Python GitHub repository starts with one command | pass | `agent` CLI and 20 fixed-SHA unfamiliar repositories |
| 2 | Current evidence determines the next action | pass | AgentController decision/execution traces |
| 3 | LLM Planner participates with rule fallback | pass | 14 planner cases, 42 runs, failure and fallback traces |
| 4 | Natural-language terminal supports LLM intent and multi-turn session | pass | 112 bilingual intent cases and `chat-ui` session restore |
| 5 | Executable actions come only from Action Registry | pass | Final registered-action rate 1.0000 and rejection cases |
| 6 | Every patch passes AST, safety and sandbox gates | pass | Unified patch validation chain |
| 7 | Failure feedback drives bounded Reflection/Replan | pass | `semantic_parse_port_reflection` and budget ablation |
| 8 | Memory affects later behavior with usage evidence | pass | 8-case memory ablation and retrieval traces |
| 9 | Terminal blockers replace fabricated success | pass | Clean-repo, environment blocker and partial outcomes |
| 10 | At least 20 unfamiliar repositories evaluated | pass | 20/20 structured reports at fixed SHAs |
| 11 | Required system ablations complete | pass | Phase 7 reports 8/8 comparisons |
| 12 | All existing and new tests pass | pass | `1234 passed in 787.62s` |
| 13 | No key, cache or unrelated output committed | pass | Release hygiene 5/5 |
| 14 | README commands work from a clean source snapshot | pass | Clean `git archive`, 25 release tests passed |
| 15 | Ten-minute live demonstration is prepared | pass | [Phase 8 demo guide](phase8_demo_guide_cn.md) |
| 16 | Resume metrics trace to experiment artifacts | pass | [Chinese resume/interview pack](../career/v2_resume_interview_pack_cn.md) |
| 17 | Project remains an algorithmic code Agent | pass | Program analysis, evidence fusion, planning, memory and reflection |

## Evidence Index

- [Architecture and algorithm design](architecture_and_design.md)
- [Five case studies](phase8_case_studies.md)
- [Ten-minute demonstration guide](phase8_demo_guide_cn.md)
- [Phase 6 unfamiliar-repository evaluation](phase6_unfamiliar_repository_robustness.md)
- [Phase 7 system evaluation](phase7_artifacts/phase7_system_evaluation.md)
- [Machine-readable Phase 8 verification](phase8_release_verification.json)
- [Chinese resume and interview pack](../career/v2_resume_interview_pack_cn.md)
- [Detailed study guide](../career/agent_project_study_interview_guide.md)

## Capability Boundaries

- The supported scope is public Python repositories, not every language or private dependency environment.
- All 20 unfamiliar repositories produced static reports, but only 7 started and terminated a test process.
- Phase 7 LLM patch and planner metrics use deterministic offline fixtures; they are not live-provider repair rates.
- Graph and dynamic evidence showed no uplift on the current rule-detectable localization benchmark.
- Without a test oracle, the Agent can emit an analysis or candidate, but not `verified_repair`.
- LLM Judge is advisory; sandbox targeted tests and full regression remain authoritative.
