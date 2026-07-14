# Phase 6 Unfamiliar Repository Robustness

## Result

Phase 6 validates the repository intake and diagnosis path against 20 public
Python repositories that were not used to implement the Phase 1-5 algorithms.
Every repository is pinned to a 40-character commit SHA. The holdout covers
small libraries, command-line tools, web and UI frameworks, data-processing and
machine-learning projects, native extensions, flat and `src` layouts,
multi-package repositories, and tox/nox projects.

The final acceptance run produced:

| Metric | Result |
| --- | ---: |
| Fixed-SHA repositories | 20 / 20 |
| Structured intelligence reports | 20 / 20 |
| Static analysis success | 20 / 20 |
| Source-root discovery | 20 / 20 |
| Test-command discovery | 12 / 20 |
| Test processes started and explicitly terminated | 7 / 20 |
| Blocker classification | 13 / 13 |
| Started-test failure-layer classification | 6 / 6 |
| Suite expectation checks | 20 / 20 |
| Suite metric checks | 40 / 40 |
| API-rate-limit checkout recoveries | 9 |
| Focused Phase 6 regression | 284 passed |
| Complete repository regression | 1223 passed |

The outcome labels are intentionally conservative. `success` means that static
analysis completed and a repository test process was actually started and
terminated with a recorded result. It does not mean that the repository tests
passed or that the Agent repaired a defect. Seven cases met this contract; one
test run passed and six terminated with classified environment failures.
`partial` means that static analysis completed but test execution did not start
within the configured safety, environment, or action budget. Thirteen cases
were partial. No case was counted as a structured success without a report.

## Holdout Protocol

The manifest is
`datasets/github_cases/v2_unfamiliar_python_repositories_20.json`. Its selection
contract requires:

- 20 unique owner/repository pairs;
- one immutable commit SHA per repository;
- no exact owner/repository reference in the tracked implementation, tests,
  documentation, or datasets at selection time;
- category coverage across layout, packaging, framework, and repository-size
  variation;
- no live LLM credentials during the robustness experiment;
- no automatic dependency installation;
- an eight-second repository-test execution limit;
- a maximum of 12 selected Python source files and five static candidates per
  repository.

Seven entries explicitly authorize repository checkout and a bounded test
probe. The remaining 13 are static-analysis cases. If GitHub's unauthenticated
Tree API is rate-limited, a static case may materialize a fixed-SHA archive only
to recover source discovery. That fallback disables test execution, environment
setup, retry prerequisites, and automatic retry unless execution checkout was
already authorized by the manifest.

## Intake And Source Discovery

The normal discovery order is:

1. Reuse an explicitly preferred matching discovery artifact when configured.
2. Query the GitHub recursive Tree API for the requested commit.
3. On API rate limiting, reuse a matching local discovery artifact when one is
   available.
4. Otherwise materialize the fixed-SHA GitHub archive and build a local source
   inventory.
5. Emit the selected path, cache source, fallback reason, HTTP status, remaining
   API quota, checkout mode, and whether execution checkout was originally
   requested.

The archive path is not an implicit permission to execute repository code.
Source-only fallback is recorded as
`github-api-rate-limit-source-checkout` with checkout mode `source_only`.
Execution-authorized fallback uses mode `test_execution`.

## Layout And Structure Modeling

Repository layout classification combines Python paths, package markers,
project configuration, test paths, and sampled source files. It reports source
roots, analysis roots, test roots, package roots, project configuration roots,
and layout kind.

Directory exclusion is repository-context aware. Runtime and cache directories
such as `.git`, `.venv`, `__pycache__`, and `.pytest_cache` are always excluded.
Artifact names such as root-level `build/` and `dist/` are excluded only at the
repository root. A legitimate package such as `src/build/` remains analyzable.
Nested `pyproject.toml` files under tests, fixtures, docs, and examples do not by
themselves classify a repository as a monorepo.

After source selection, the existing AST, call graph, program graph, static
signal mining, and function-level ranking pipeline runs normally. The holdout
is therefore testing the same repository intelligence path used by the Agent,
not a separate metadata-only smoke check.

## Bounded Source Selection

Large repositories can contain hundreds or thousands of Python files. The
selector first performs an O(N) metadata pass over paths and layout signals. It
then uses path score and directory/package diversity to select at most `k`
files, where `k=12` in this experiment. Content scoring is applied immediately
only to locally materialized files. Remote Tree API entries are not all fetched
to compute a score; only the selected files are downloaded later.

This changes remote source transfer from O(N) requests to O(k) requests while
preserving deterministic path and diversity ranking. Archive fallback still
downloads one bounded repository archive, so its wall time remains sensitive to
network speed and repository size.

## Compatibility Assessment

`repository_compatibility.json` separates a configuration snapshot from an
executable checkout. A downloaded `pyproject.toml` is sufficient for packaging
and Python-version diagnosis, but it is not reported as a runnable repository
root.

The compatibility assessor records:

- repository scope and Python-source availability;
- Python requirement compatibility with the current interpreter;
- dependency access blockers such as local paths, VCS requirements, URLs, and
  credential references;
- install risk and whether automatic installation is authorized;
- configuration-root presence and execution-root presence;
- readiness, primary blocker, explicit termination reason, and next action.

This prevents an environment incompatibility from being reported as a code
defect and prevents a partial configuration snapshot from being presented as a
full checkout.

## Test Diagnosis And Failure Layers

Test discovery reads repository configuration and CI metadata without executing
packaging scripts. It identifies pytest, unittest, tox, and nox signals and
builds bounded candidate commands. A test command runs only when the case
authorizes checkout and the execution plan is safe.

The setup doctor evaluates checkout availability, interpreter compatibility,
test-tool availability, configuration, setup status, execution status, and
dynamic evidence. Sentinel values such as `none` are normalized as no setup
failure rather than a false blocker.

Started test failures are mapped to a failure layer:

| Layer | Representative categories |
| --- | --- |
| Environment | missing dependency, missing runner, collection/configuration error, timeout, missing interpreter |
| Code | assertion failure, syntax error |
| None | passing test process |
| Unknown | insufficient evidence; never silently converted to code failure |

All six failing started tests in the holdout were classified as environment
failures. The experiment does not claim that these repositories contain defects.

## Acceptance Contract

The independent evaluator requires all of the following:

1. At least 20 unique fixed-SHA cases.
2. A nonempty structured report for every case.
3. Successful source import, repository structure modeling, and graph modeling
   for every static-analysis success.
4. An explicit termination reason for every success, partial, or blocked case.
5. A classified environment/code layer for every started failing test.
6. A classified blocker for every partial or blocked case.

The resulting rates were 1.0000 for structured reports, static analysis,
source-root discovery, blocker classification, and test failure-layer
classification. Test-command discovery was 0.6000 and test start rate was
0.3500 because only seven cases authorized a checkout probe.

The focused Phase 6 regression completed with 284 passing tests in 126.40
seconds. The complete repository regression completed with 1223 passing tests,
zero failures, in 855.94 seconds on the recorded development environment.

## Timing Provenance

The final 20-case suite was aggregated with `--reuse-existing-reports` after the
real repository runs completed. Its per-run elapsed values therefore measure
JSON report loading, not end-to-end repository analysis. The evaluator marks
these values as `report_reuse_overhead` and
`end_to_end_elapsed_available=false`. They must not be presented as Agent
latency. Comparable cold/warm latency and network-transfer measurements belong
to the Phase 7 benchmark.

## Reproduction

Run the 20 fixed-SHA repository suite:

```powershell
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite `
  datasets/github_cases/v2_unfamiliar_python_repositories_20.json `
  outputs_v2/phase6/unfamiliar_repositories_fixed `
  --format json `
  --require-success
```

Aggregate existing reports without another network run:

```powershell
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite `
  datasets/github_cases/v2_unfamiliar_python_repositories_20.json `
  outputs_v2/phase6/unfamiliar_repositories_fixed `
  --reuse-existing-reports `
  --format json `
  --require-success
```

Run the independent outcome evaluator:

```powershell
python -m code_intelligence_agent.evaluation.unfamiliar_repository_evaluation `
  datasets/github_cases/v2_unfamiliar_python_repositories_20.json `
  outputs_v2/phase6/unfamiliar_repositories_fixed/github_repo_intelligence_suite.json `
  outputs_v2/phase6/unfamiliar_repository_evaluation `
  --require-success
```

## Boundaries

- The experiment covers public Python repositories, not arbitrary languages or
  private repositories.
- Static-analysis success is not proof that all project modules were analyzed;
  the source budget deliberately samples representative files.
- Thirteen cases did not start tests, so this phase is a repository-intake and
  diagnosis robustness result, not a 20-repository repair benchmark.
- Dependency installation remained disabled. Projects requiring compiled
  extensions, services, credentials, or unsupported Python versions terminate
  with an environment blocker.
- Live LLM quality and repair success are evaluated separately. No API key or
  raw model prompt is stored in the holdout artifacts.
