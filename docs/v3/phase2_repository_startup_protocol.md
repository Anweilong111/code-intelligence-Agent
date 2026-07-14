# V3 Phase 2: Isolated Repository Test Startup Protocol

## Purpose

Phase 2 measures whether the Agent can discover, start, and explicitly terminate
a real test process for an unfamiliar fixed-SHA Python repository without first
executing repository installation hooks. It addresses the V2 engineering
bottleneck where only 7 of 20 repositories reached a started test process.

This phase does not measure test-suite pass rate and does not claim that a defect
was localized or repaired. A missing dependency after pytest starts is a valid
startup observation, not a verified repair.

## Paired Dataset

The manifest `datasets/github_cases/v3_fixed_sha_repository_startup_20.json`
contains the same 20 `(repository, commit SHA)` pairs as
`datasets/github_cases/v2_unfamiliar_python_repositories_20.json`. Every V3 run
uses the `checkout` execution profile and authorizes repository-test checkout.

The comparison is paired by repository and commit, but the execution protocols
are not identical:

| Property | V2 | V3 Phase 2 |
| --- | --- | --- |
| Fixed repositories | 20 | same 20 |
| Fixed SHAs | 20 | same 20 |
| Test checkout authorized | 7/20 | 20/20 |
| Setup mode | project-oriented diagnosis | isolated `runner_probe` |
| Repository code install | protocol-dependent | forbidden by this probe |
| Repository dependency install | protocol-dependent | forbidden by this probe |

Therefore the 7/20 to 19/20 change is evidence that the new authorized isolated
startup protocol reaches more test processes. It must not be presented as a
pure model or algorithm uplift under an unchanged protocol.

## Why A Runner Probe Exists

Installing an unfamiliar project can execute `setup.py`, PEP 517 build hooks,
compiled build steps, plugin hooks, or arbitrary dependency code. Phase 2 needs
to distinguish two questions:

1. Can the Agent safely obtain a known test runner and start a repository test?
2. Can the Agent build the complete project environment and pass its suite?

The `runner_probe` mode isolates question 1. It creates a fresh venv and installs
only pytest. It never translates the repository's Poetry, uv, tox, nox, editable
install, extras, or project dependency command into an executed install action.
Those signals are still diagnosed and reported for later environment repair.

## Startup Algorithm

### 1. Observe repository compatibility

The Agent reads Python version constraints, test framework signals, configuration
files, candidate test paths, working-directory hints, and discovered commands.
An incompatible Python constraint is a hard startup blocker for this probe.

### 2. Plan an isolated runner environment

For run `i`, the setup planner constructs:

```text
venv_i = output_i/.repo_test_venv
create_i = current_python -m venv venv_i
install_i = venv_i/python -m pip install pytest
```

The plan records `setup_mode=runner_probe`, `test_module=pytest`, whether code or
dependencies were requested, install risk, Python compatibility, venv path, and
the safety boundary. Unknown setup modes are rejected.

### 3. Execute setup only when the plan is ready

The venv create and pytest install commands run as argument arrays rather than
free-form shell text. Each command has a timeout and a captured return code,
stdout, stderr, and reason. If compatibility or setup readiness fails, execution
is skipped with an explicit blocker.

### 4. Normalize the test command

The execution planner ranks repository-profile and CI command candidates. When
the repository prefers tox or nox but the prepared isolated runner is pytest,
the planner may synthesize a narrow `python -m pytest -q` candidate and attach
the reason `prepared_runner_probe`. Selected test paths and working directories
remain bounded by the existing planner.

Nine of the 20 real runs used this audited fallback: seven from tox and two from
nox. This fallback changes only the runner entry point; it does not claim to
reproduce the complete tox/nox environment matrix.

### 5. Enforce the no-host-fallback gate

The test command is executable only when all required conditions hold:

```text
Executable = command_seed
             AND checkout_present
             AND candidate_exists
             AND runner_probe_setup_ready
             AND selected_runner_prepared
             AND working_directory_present
             AND selected_paths_present
```

If `runner_probe` was requested but setup did not produce the prepared pytest
runner, the planner emits `runner_probe_setup_not_ready` and refuses to use the
current interpreter. This prevents a nominally isolated evaluation from silently
running against host packages.

### 6. Start, bound, and classify the test process

The planned pytest command runs with an 8-second test timeout in the fixed
manifest. Completion, non-zero exit, and timeout are all explicit termination
outcomes. The execution classifier separates missing dependencies, collection
errors, command usage, timeout, missing native extension, and other categories.

`missing_native_extension` was added after the Polars run exposed the precise
message `Polars binary is missing!`; keeping it separate avoids hiding a native
build blocker under generic `command_failed`.

### 7. Preserve audit evidence

Setup mode, code/dependency install flags, venv source, execution status,
termination status, failure category, blocker, and report path propagate through
onboarding, Agent, intelligence, and suite summaries. The dedicated evaluator
reconstructs every case and rejects the result if any started process used the
host interpreter or any repository install was requested.

## Acceptance Contract

A case counts as `StartedAndTerminated` only when all clauses are true:

```text
StartedAndTerminated_i = test_started_i
                         AND explicit_status_i
                         AND setup_mode_i == runner_probe
                         AND setup_executed_i
                         AND setup_status_i == pass
                         AND python_source_i == repository_test_environment_setup
                         AND NOT repository_code_install_i
                         AND NOT repository_dependency_install_i
```

Phase 2 passes when:

- exactly 20 unique fixed-SHA repositories are evaluated;
- all 20 have structured reports;
- no repository code or dependency install is requested;
- every started process uses the isolated venv and terminates explicitly;
- at least 14 of 20 satisfy the startup contract;
- every non-started case has a blocker;
- every started failure has a failure layer.

## Result

| Metric | Result |
| --- | ---: |
| Structured reports | 20/20 |
| Test commands discovered | 20/20 |
| Setup executed successfully | 19/20 |
| Test processes started and explicitly terminated | 19/20 |
| Startup rate | 0.9500 |
| Started processes using isolated venv | 19/19 |
| Repository code installs requested | 0 |
| Repository dependency installs requested | 0 |
| Test process statuses | 1 pass, 18 fail, 1 skipped |
| Started failures with classified layer | 18/18 |
| V2 paired baseline | 7/20 (0.3500) |
| Count/rate difference | +12 / +0.6000 |

The 18 started failures are all environment-layer outcomes: 14 missing Python
dependencies, one missing native extension, one pytest collection error, one
command usage error, and one timeout. The only non-started case is Typer, whose
fixed SHA does not accept the current Python 3.13 runtime.

## Reproduction

Run or refresh the 20 repositories:

```powershell
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite `
  datasets/github_cases/v3_fixed_sha_repository_startup_20.json `
  outputs_v3/phase2/repository_startup_20 `
  --format markdown
```

Aggregate existing structured reports without rerunning repositories:

```powershell
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite `
  datasets/github_cases/v3_fixed_sha_repository_startup_20.json `
  outputs_v3/phase2/repository_startup_20 `
  --reuse-existing-reports `
  --format markdown `
  --require-success
```

Run the strict startup evaluator:

```powershell
python -m code_intelligence_agent.evaluation.v3_repository_startup_evaluation `
  datasets/github_cases/v3_fixed_sha_repository_startup_20.json `
  outputs_v3/phase2/repository_startup_20/github_repo_intelligence_suite.json `
  outputs_v3/phase2/repository_startup_evaluation `
  --baseline-metrics docs/v2/phase6_unfamiliar_repository_metrics.json `
  --minimum-started 14 `
  --require-success
```

Raw checkouts, venvs, and per-run reports remain under ignored `outputs_v3/`.
The committed manifest, evaluator, tests, protocol, and aggregate metrics are the
portable evidence surface.

## Limits And Next Phase

- The probe installs only pytest, so 18 environment failures are expected and
  are not code-repair failures.
- A pytest fallback is not equivalent to executing the repository's complete
  tox, nox, Poetry, or uv matrix.
- Isolation currently consists of a per-case venv, bounded command time, and
  execution-policy gates. Container-level CPU, memory, disk, and network limits
  remain security work for Phase 6.
- The experiment does not call a live LLM and does not produce pass@1, pass@3,
  token cost, latency, reflection recovery, or verified repair rate.
- Phase 3 uses the accepted real-bug benchmark to evaluate real Rule, LLM, and
  Hybrid patch generation under sandbox test oracles.
