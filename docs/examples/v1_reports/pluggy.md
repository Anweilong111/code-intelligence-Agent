# V1 Sample: `pytest-dev/pluggy`

## Input

| Field | Value |
| --- | --- |
| Repository | `https://github.com/pytest-dev/pluggy` |
| Ref | `7fce99cb955846901b22b051909aa4f30dc16128` |
| Profile | `agent-auto` |
| Scenario | GitHub URL input, pinned ref, src-layout repository slice |

## Agent Loop

| Step | Evidence |
| --- | --- |
| Observe | Parsed 2 Python files, 10 functions, 2 classes, and 106 LOC from the `src/pluggy` slice. |
| Plan | Use static structure and graph evidence first, then collect repository-test evidence before repair. |
| Act | Built structure summary, graph summary, static rule signals, and Top-k suspicious-function ranking. |
| Verify | Static localization was available and objective-compliance gates passed. |
| Reflect | Dynamic failing-test evidence was still unavailable, so direct repair would be under-supported. |
| Replan | Selected `discover_repository_tests` to retry with full checkout and broader source/test discovery. |

## Repository Understanding

| Metric | Value |
| --- | ---: |
| Analyzed Python files | 2 |
| Functions | 10 |
| Classes | 2 |
| LOC | 106 |
| Max cyclomatic complexity | 4 |

| Field | Value |
| --- | --- |
| Layout | `monorepo_candidate` |
| Recommended analysis roots | `src/pluggy` |
| Package roots | `src` |
| Src-layout packages | `pluggy` |

## Top-k Localization

| Rank | Function | File | Final Score | Source Role | Rule IDs | Bug Types |
| ---: | --- | --- | ---: | --- | --- | --- |
| 1 | `TagTracer.get` | `pluggy/_tracing.py` | `1.0` | `application` | `mutable_default_arg` | `state leakage` |

## Controller Decision

| Field | Value |
| --- | --- |
| Current stage | `phase2_static_graph_fault_localization` |
| Next stage | `phase3_repository_test_execution` |
| Primary blocker | `dynamic_evidence_not_usable` |
| Selected action | `discover_repository_tests` |
| Confidence | `0.76` |
| Risk | `low` |

Reason:

> Static localization is available, but the repository test entrypoint is
> missing or collected no tests. The Agent should retry with a full shallow
> checkout and broader source/test discovery before switching to synthetic
> overlay or external bug input.

## Final Audit

| Check | Result |
| --- | --- |
| Agent status | `pass` |
| Objective compliance | `pass` |
| Answer coverage complete | `true` |
| Repair success claim | `not_claimed` |

This example is useful because it shows a realistic src-layout GitHub URL run:
the Agent can rank an application function, but still waits for stronger
dynamic evidence before attempting repair.
