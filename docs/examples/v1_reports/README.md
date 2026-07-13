# V1 Sample Report Pack

This directory contains tracked, compact report summaries for three public
GitHub repository runs. The full runtime artifacts are intentionally not
committed; each page below captures the audit evidence needed for GitHub
showcase and interview review.

## Cases

| Case | Repository | Scenario | Stage | Blocker | Selected Action | Top Function |
| --- | --- | --- | --- | --- | --- | --- |
| [pypa/sampleproject](pypa_sampleproject.md) | `pypa/sampleproject` | src-layout package with nox/test automation signal | `phase2_static_graph_fault_localization` | `dynamic_evidence_not_usable` | `adjust_application_source_focus` | `build_and_check_dists` |
| [pytest-dev/pluggy](pluggy.md) | `pytest-dev/pluggy` | GitHub URL input, pinned ref, src-layout slice | `phase2_static_graph_fault_localization` | `dynamic_evidence_not_usable` | `discover_repository_tests` | `TagTracer.get` |
| [octocat/Hello-World](octocat_hello_world.md) | `octocat/Hello-World` | no Python source blocker | `source_import_blocked` | `source_import_or_parse_missing` | `adjust_source_filters` | none |

## Suite Evidence

| Metric | Value |
| --- | ---: |
| Runs | 3 |
| Agent passed runs | 3 |
| Expectation failures | 0 |
| Metric check failures | 0 |
| Discovery cache reuse runs | 3 |
| Acceptance gate pass runs | 3 |
| Agent goal readiness pass runs | 3 |
| Objective compliance pass runs | 3 |
| Agent controller loop complete runs | 3 |
| Agent decision timeline complete runs | 3 |
| Repository structure modeled runs | 2 |
| Repo graph ready runs | 2 |
| Program graph available runs | 2 |
| Source import blocked runs | 1 |

## What This Demonstrates

- A public GitHub input can be turned into an auditable Agent report.
- The controller records `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`
  evidence for each run.
- The system distinguishes normal Python repo analysis from terminal blockers.
- The Agent does not over-claim repair when failing-test evidence is unavailable.
- Final reports include repo profile, structure/graph status, Top-k localization
  or blocker state, controller action, and objective-compliance audit.

This pack is a compact showcase. The broader v1 target remains the 30-repo
onboarding catalog and 50-case repair/evaluation catalog.
