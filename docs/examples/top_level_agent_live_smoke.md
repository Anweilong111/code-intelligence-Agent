# Top-Level Agent Live Smoke: Public Repositories

This report captures a live run through the top-level Agent entrypoint. It is
intended to prove that users do not need to call the internal evaluation module
directly: a public GitHub repository URL can be passed to the package-level
command and still produce the full audit artifact set.

The smoke evidence covers two important arbitrary-repository outcomes:

- `pytest-dev/iniconfig`: tests pass cleanly, no static defect candidates are
  produced, and the Agent stops with an auditable `no_static_candidates`
  decision instead of inventing a repair.
- `pallets/itsdangerous`: repository tests expose an environment blocker, and
  the Agent records an environment-repair plan instead of treating setup
  failure as application evidence.

## Zero-Exit Clean-Repository Smoke: `pytest-dev/iniconfig`

This run was executed against a repository that is not part of the 30-case V1
onboarding manifest. It validates the top-level Agent entrypoint on a new public
GitHub repository where the correct outcome is an analysis report, not a patch.

### Command

```bash
python -m code_intelligence_agent agent ^
  https://github.com/pytest-dev/iniconfig ^
  <output_dir> ^
  --preset mining ^
  --max-sources 25 ^
  --max-candidates 12 ^
  --checkout-repository-tests ^
  --repository-checkout-depth 1 ^
  --repository-test-timeout 20 ^
  --auto-controller-actions ^
  --auto-controller-max-actions 2 ^
  --format json
```

### Repository Understanding

| Field | Value |
| --- | --- |
| Repository | `pytest-dev/iniconfig` |
| Ref | `main` |
| Input kind | GitHub URL |
| Layout | `src_layout` |
| Recommended analysis root | `src/iniconfig` |
| Test directory | `testing` |

| Metric | Value |
| --- | ---: |
| Analyzed Python files | 5 |
| Functions | 66 |
| Classes | 4 |
| LOC | 843 |
| Max cyclomatic complexity | 15 |
| File graph nodes | 5 |
| Function graph nodes | 56 |
| Function call edges | 24 |
| File dependency edges | 3 |
| Audit artifacts | 52 |

### Test Diagnosis

| Field | Value |
| --- | --- |
| Planned command | `python -m pytest testing` |
| Result status | `pass` |
| Test count | 49 |
| Passed | 49 |
| Failed | 0 |
| Errors | 0 |
| Dynamic evidence level | `passing_tests` |

### AgentController Trace

| Iteration | Observe | Plan | Act | Verify | Reflect | Replan |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | Repository parsed, graph built, tests passed, static signal count is `0` | Select `run_repository_tests_with_checkout` because static mining produced no candidates | Stop because the checkout-backed test action was already applied | `selected_action_already_applied` | No application failure to repair | Recommend broader analysis scope or external dynamic evidence |

Loop contract: `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`.

### Final Audit

| Check | Result |
| --- | --- |
| Final status | `pass` |
| Status reason | `no_static_candidates_report_ready` |
| Primary blocker | `no_static_candidates` |
| Fault localization | `skipped` |
| Fault localization reason | `static_fallback_no_dynamic_ranking` |
| Controller status | `ready` |
| Loop iteration audit | `pass` |
| Complete Agent loop recorded | `true` |
| Artifact inventory | `pass` |
| Repair success claim | `not_claimed` |

This is the expected result for a healthy small library: the Agent can explain
the repository and validate tests, but it does not fabricate a suspicious
function or patch when there is no static or dynamic defect signal.

## Environment-Blocker Smoke: `pallets/itsdangerous`

## Command

```bash
python -m code_intelligence_agent agent ^
  https://github.com/pallets/itsdangerous ^
  <output_dir> ^
  --preset mining ^
  --max-sources 25 ^
  --max-candidates 12 ^
  --checkout-repository-tests ^
  --repository-checkout-depth 1 ^
  --repository-test-timeout 20 ^
  --format json
```

## Repository Understanding

| Field | Value |
| --- | --- |
| Repository | `pallets/itsdangerous` |
| Ref | `main` |
| Input kind | GitHub URL |
| Layout | `src_layout` |
| Recommended analysis root | `src/itsdangerous` |
| Test directory | `tests/test_itsdangerous` |

| Metric | Value |
| --- | ---: |
| Analyzed Python files | 15 |
| Functions | 115 |
| Classes | 29 |
| LOC | 1,712 |
| Max cyclomatic complexity | 14 |
| File graph nodes | 15 |
| Function graph nodes | 106 |
| Function call edges | 96 |
| File dependency edges | 26 |

## Top-k Localization

| Rank | Function | File | Final Score | Source Role | Rule IDs | Bug Types |
| ---: | --- | --- | ---: | --- | --- | --- |
| 1 | `base64_decode` | `itsdangerous/encoding.py` | `1.0` | `application` | `broad_exception_pass` | `exception handling error` |

Why suspicious:

> `base64_decode` has a static broad-exception signal, is an application
> function, and receives both static-rule and graph scores of `1.0`.

## Test Diagnosis

| Field | Value |
| --- | --- |
| Planned command | `python -m pytest -q tests` |
| Dynamic evidence level | `environment_failure` |
| Result status | `fail` |
| Test count | 4 |
| Passed | 0 |
| Failed | 4 |
| Errors | 4 |
| Setup blocker | `setup_install_failure:none` |
| Missing module | `freezegun` |
| Recommended install command | `uv sync --dev` |
| Fallback install hint | `python -m pip install freezegun` |

The Agent did not treat the failed test command as usable defect evidence,
because the failure was classified as environment setup rather than a
localizable application failure.

## AgentController Trace

| Iteration | Observe | Plan | Act | Verify | Reflect | Replan |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | `phase2_static_graph_fault_localization` with `dynamic_evidence_not_usable:environment_failure` | `prepare_repository_test_environment` | Executed environment-repair planning | `environment_repair_plan_recorded` | `verified_progress` | Stop when goal readiness became pass; next action `await_environment_repair` |
| 2 | Same blocker, goal readiness pass | `await_environment_repair` | Stopped because action is not executable now | `selected_action_not_executable` | Manual/environment blocker | Apply environment repair, then rerun Agent |

Selected final action:

| Field | Value |
| --- | --- |
| Action | `await_environment_repair` |
| Phase | `phase3` |
| Status | `blocked` |
| Confidence | `0.82` |
| Risk | `medium` |
| Executable now | `false` |
| Requires user action | `true` |
| Requires environment change | `true` |

Reason:

> Repository test environment repair advice has been recorded; external
> dependency or tool setup is required before dynamic localization can continue.

## Final Audit

| Check | Result |
| --- | --- |
| Static intelligence status | `analysis_ready` |
| Acceptance gate | `pass` |
| Agent goal readiness | `pass` |
| Answer coverage complete | `true` |
| Objective compliance | `pass` |
| Objective compliance sections | `6/6` |
| Artifact inventory | `pass` |
| Repair success claim | `not_claimed` |

This live smoke is intentionally a blocker case. It demonstrates the Agent
behavior required for arbitrary repositories: the system proceeds through repo
understanding, graph construction, test diagnosis, Top-k localization, and
controller reasoning, then stops with an auditable environment-repair plan
instead of fabricating a patch or crashing on missing dependencies.
