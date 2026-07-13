# Agent Demo Artifact Checklist

This page is the checklist for resume and interview demos. Each demo should
show the main report plus the three Agent-specific audit reports, so reviewers
can distinguish real execution, planning, memory reuse, and blocker handling.

## Required Artifacts Per Demo

| Artifact | Why It Matters |
| --- | --- |
| `github_repo_intelligence.md` | Main repository analysis report: repo profile, graph/static signals, test diagnosis, localization, repair/blocker result |
| `agent_execution_trace.md` | Shows whether each Agent action was planned, executed, skipped, blocked, failed, or verified |
| `agent_decision_report.md` | Shows controller-selected action, LLM planner proposal, safety gate decision, fallback, and next plan |
| `agent_memory_report.md` | Shows session/repo/repair/pattern memory and whether memory feeds patch generation, reflection, and replan |

## Demo Matrix

| Demo | Repository | Scenario | Existing Summary | Generated Output Directory |
| --- | --- | --- | --- | --- |
| Normal analysis | `pytest-dev/pluggy` | Public Python repo, pytest discovered, tests pass, Agent reports regression guard instead of inventing a bug | [testable_repo.md](testable_repo.md) | `outputs_smoke/repo_intelligence_p3_product_robustness_current/pluggy_p3_src_layout_pinned/` |
| Test/blocker handling | `pypa/sampleproject` | Repository tests are discovered and executed; Agent records verified progress, then blocks on insufficient application-source focus/dynamic evidence instead of inventing a fix | [v1_reports/pypa_sampleproject.md](v1_reports/pypa_sampleproject.md) | `outputs_smoke/repo_intelligence_p3_product_robustness_current/pypa_sampleproject_p3_environment_blocker/` |
| Repair reflection | `TheAlgorithms/Python` | Top-k localization, failed initial patch, reflection-generated refined patch, sandbox validation success | [repair_reflection_repo.md](repair_reflection_repo.md) | `outputs_smoke/repo_intelligence_p3_product_robustness_current/thealgorithms_p3_repair_reflection/` |

Optional extra blocker demo: `octocat/Hello-World` remains useful for showing
the no-Python-source path, but the required blocker sample above is a Python
repository path with test execution evidence and a controller blocker.

## LLM Planner Evidence To Point Out

For agent-auto runs, inspect `agent_decision_report.md` and
`github_repo_agent_controller.md`:

- `llm_planner.selected_action`: the LLM planner proposal when configured.
- `llm_planner.memory_used`: session/repo/repair memory used in planning.
- `llm_planner.fallback_to_rule_planner`: true when no replan key is available.
- `llm_planner.safety_gate`: whether the proposal matched the controller action,
  was advisory-only, blocked, or fell back to the rule planner.

The interview-safe explanation is: the LLM proposes the plan, but action
registry, risk policy, and sandbox validation retain final authority.
