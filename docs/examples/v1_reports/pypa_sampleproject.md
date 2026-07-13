# V1 Sample: `pypa/sampleproject`

## Input

| Field | Value |
| --- | --- |
| Repository | `pypa/sampleproject` |
| Ref | `main` |
| Profile | `agent-auto` |
| Scenario | src-layout Python package with nox/test automation signal |

## Agent Loop

| Step | Evidence |
| --- | --- |
| Observe | Parsed 5 Python files, 6 functions, 1 class, and 77 LOC. Detected `src_layout` with recommended analysis root `src/sample`. |
| Plan | Continue from repository understanding to static bug-signal mining and function-level localization. |
| Act | Built repository structure, call/program graph summaries, static rule signals, and Top-k suspicious-function ranking. |
| Verify | Static report and objective-compliance gates passed, but dynamic evidence was not usable for repair. |
| Reflect | Top-ranked signal came from test automation rather than application code, so the Agent avoided claiming an application repair target. |
| Replan | Selected `adjust_application_source_focus` and recommended retargeting source mining before repair. |

## Repository Understanding

| Metric | Value |
| --- | ---: |
| Analyzed Python files | 5 |
| Functions | 6 |
| Classes | 1 |
| LOC | 77 |
| Max cyclomatic complexity | 1 |

| Field | Value |
| --- | --- |
| Layout | `src_layout` |
| Recommended analysis roots | `src/sample` |
| Package roots | `src` |
| Test directories | `tests` |

## Top-k Localization

| Rank | Function | File | Final Score | Source Role | Rule IDs | Bug Types |
| ---: | --- | --- | ---: | --- | --- | --- |
| 1 | `build_and_check_dists` | `sample/noxfile.py` | `0.9325` | `test_automation` | `broad_exception_pass` | `exception handling error` |

## Controller Decision

| Field | Value |
| --- | --- |
| Current stage | `phase2_static_graph_fault_localization` |
| Next stage | `phase3_repository_test_execution` |
| Primary blocker | `dynamic_evidence_not_usable` |
| Selected action | `adjust_application_source_focus` |
| Confidence | `0.76` |
| Risk | `low` |

Reason:

> Static Top-k currently contains no application-source candidates. The top
> source role is test automation, so the Agent should broaden or retarget source
> mining before treating the finding as an application bug.

## Final Audit

| Check | Result |
| --- | --- |
| Agent status | `pass` |
| Objective compliance | `pass` |
| Answer coverage complete | `true` |
| Repair success claim | `not_claimed` |

This example is useful because it shows conservative Agent behavior: the system
keeps the analysis auditable instead of forcing a patch when the strongest
signal points at automation code.
