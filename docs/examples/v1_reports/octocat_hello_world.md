# V1 Sample: `octocat/Hello-World`

## Input

| Field | Value |
| --- | --- |
| Repository | `https://github.com/octocat/Hello-World` |
| Ref | `master` |
| Profile | `agent-auto` |
| Scenario | Public GitHub repository with no analyzable Python source |

## Agent Loop

| Step | Evidence |
| --- | --- |
| Observe | Source import found 0 Python files, 0 functions, 0 classes, and 0 LOC. |
| Plan | Stop normal Python analysis and move into source-import blocker handling. |
| Act | Generated source-import, repository-profile, controller, readiness, and final audit artifacts. |
| Verify | Confirmed that structure graph, program graph, dynamic tests, and repair are not attemptable without Python source. |
| Reflect | The repository is not a valid Python-analysis target under current filters. |
| Replan | Selected `adjust_source_filters` and recommended changing include/exclude filters or selecting a Python repository. |

## Repository Understanding

| Metric | Value |
| --- | ---: |
| Analyzed Python files | 0 |
| Functions | 0 |
| Classes | 0 |
| LOC | 0 |

| Field | Value |
| --- | --- |
| Layout | `no_python_source` |
| Recommended analysis roots | none |
| Test directories | none |

## Controller Decision

| Field | Value |
| --- | --- |
| Current stage | `source_import_blocked` |
| Next stage | `phase1_repo_understanding` |
| Primary blocker | `source_import_or_parse_missing` |
| Selected action | `adjust_source_filters` |
| Confidence | `0.76` |
| Risk | `low` |

Reason:

> Repository source import or parsing did not produce analyzable Python files.
> The Agent should relax narrow include or target-prefix filters and rerun
> source discovery.

## Final Audit

| Check | Result |
| --- | --- |
| Agent status | `pass` |
| Objective compliance | `pass` |
| Answer coverage complete | `true` |
| Repair success claim | `not_claimed` |

This example is important because it proves blocker behavior. The Agent does
not crash or fabricate analysis when a repository has no Python source; it
returns an auditable blocker and next action.
