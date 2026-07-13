# V1 Sample Reports

This page indexes the compact v1 sample-report pack for three public GitHub
repositories. The samples demonstrate normal Python onboarding, src-layout
analysis, and a terminal source blocker without claiming that the full v1 30/50
evaluation target is complete.

## Reproduce

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite ^
  datasets/github_cases/repo_intelligence_v1_sample_reports.example.json ^
  outputs/v1_sample_reports ^
  --format json --require-success
```

For one-command Agent usage on a new repository:

```bash
python -m code_intelligence_agent agent https://github.com/pytest-dev/pluggy --format markdown
```

## Tracked Report Pack

| Case | Repository | Stage | Blocker | Selected Action | Tracked Summary |
| --- | --- | --- | --- | --- | --- |
| `pypa_sampleproject_v1_sample` | `pypa/sampleproject` | `phase2_static_graph_fault_localization` | `dynamic_evidence_not_usable` | `adjust_application_source_focus` | [summary](v1_reports/pypa_sampleproject.md) |
| `pluggy_v1_src_layout_sample` | `pytest-dev/pluggy` | `phase2_static_graph_fault_localization` | `dynamic_evidence_not_usable` | `discover_repository_tests` | [summary](v1_reports/pluggy.md) |
| `octocat_hello_world_v1_blocker_sample` | `octocat/Hello-World` | `source_import_blocked` | `source_import_or_parse_missing` | `adjust_source_filters` | [summary](v1_reports/octocat_hello_world.md) |

Suite overview:

[v1_reports/README.md](v1_reports/README.md)

## Evidence

- Runs: 3
- Agent passed runs: 3
- Expectation failures: 0
- Metric check failures: 0
- Discovery cache reuse: 3
- Acceptance gate pass runs: 3
- Agent goal readiness pass runs: 3
- Objective compliance pass runs: 3
- Agent controller loop complete runs: 3
- Agent decision timeline complete runs: 3
- Repository structure modeled runs: 2
- Repo graph ready runs: 2
- Program graph available runs: 2
- Source import blocked runs: 1

## Scope

These reports prove that the Agent can produce auditable repo profile,
structure graph, test discovery signals, Top-k localization or blocker state,
AgentController reasoning traces, and final audit reports for public GitHub
repository inputs.

They are not a substitute for the full v1 target dataset, which is tracked by:

- `datasets/github_cases/repo_intelligence_v1_onboarding_30.example.json`
- `datasets/github_cases/llm_repair_case_catalog_v1_50.example.json`
