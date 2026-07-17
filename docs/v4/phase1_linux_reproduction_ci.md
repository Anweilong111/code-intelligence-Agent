# V4 Phase 1 Linux Reproduction CI

## Purpose

The first real Thefuck candidate proved its targeted bug/fix behavior on Windows,
but the historical full regression suite contains POSIX-only assumptions. V4 does
not exclude those tests or count the case as reproduced. The Linux CI lane runs
the same fixed SHAs and three acceptance gates on the required platform.

The workflow is defined in
`.github/workflows/v4-phase1-linux-reproduction.yml`. Automatic runs are limited
to the V4 branch and the workflow, bootstrapper, fixed catalog, selection plan,
and reproduction profile paths. Manual runs expose only three bounded choices:
the accepted baseline, the four remaining Thefuck candidates, or the complete
five-case frozen selection. Free-form commands and case IDs are not accepted.

## Runtime Construction

The runner is frozen to `ubuntu-22.04`. The host Python only runs the V4
orchestrator. The target repository tests use a separate exact Python `3.7.0`
runtime created from the runner's preinstalled Miniconda and the conda-forge full
repodata. The V4 bootstrapper then creates a copied project-isolated environment,
installs only exact binary-wheel requirements, executes `pip check`, audits every
frozen distribution with `pip freeze`, and probes the required modules. A missing
PyPI wheel may be replaced only by a profile-declared, fixed-hash conda Python
binary whose package/version, conda build, platform, Python ABI, metadata, member
paths, and native suffixes all pass validation. Extraction never executes archive
scripts.

Runtime profiles now support both Windows and Linux executable mappings. Platform
specific dependencies are also explicit: `win_unicode_console` applies only on
Windows, while the audited `psutil==5.7.0` conda binary applies only on Linux and
replaces that single pip install candidate without removing it from the frozen
distribution audit.

## Security Boundary

The workflow has only `contents: read` permission and does not reference repository
secrets, model keys, or `github.token`. It performs a detached public checkout and
removes the public remote before tests start. No repository setup script or project
installation is allowed. The only external action is `actions/upload-artifact`,
pinned to immutable commit
`043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` (`v7.0.1`).

The tested repository still runs on a hosted runner rather than the Phase 6
container sandbox. Therefore this lane is restricted to the pre-audited fixed-SHA
Thefuck selection and is not a general permission to execute arbitrary GitHub
repositories.

## Acceptance Contract

The evidence-collection job must satisfy all of the following:

1. Miniconda reports exact Python `3.7.0` from a non-symlink executable.
2. The isolated runtime bootstrap result is `pass`.
3. Every selected case is `ready` on Linux with zero planning blockers.
4. Every selected case produces parseable evidence whose case identity and
   canonical evidence fingerprint match.
5. The accepted baseline passes all reproduction gates whenever it is selected.
6. A batch summary preserves each selected case's `pass` or non-pass outcome.

A candidate's non-pass result is benchmark evidence rather than an infrastructure
failure, so it does not discard valid evidence from other cases in the same batch.
Case acceptance remains stricter and independent: the bug SHA must fail the
declared target, the fix SHA must pass that target, and the fix SHA must pass the
full declared regression command. The `accept` command revalidates those gates
from the artifact before changing the catalog.

Until the workflow produces a passing hashed reproduction artifact, no new case is
added to the accepted V4 benchmark denominator.

## First Attempt

Run `29600980774` on commit
`93a6a925c7140617ea290fe776b52e1b02a74839` failed in the isolated runtime step.
Exact Python `3.7.0` provisioning passed, but pip reported no Linux binary for
`psutil==5.7.0`. The later readiness and reproduction steps were skipped. The
failure is classified as `dependency_install_failed`, not as a benchmark test or
repair failure, and contributes zero accepted cases. The machine-readable record
is `docs/v4/phase1_linux_reproduction_attempt_1.json`.

## Second Attempt

Run `29605898564` on commit
`6cce0399e9dc9888f014f8f20a99a5129baae9b1` passed exact runtime construction,
all 20 frozen requirement checks, `pip check`, all seven module probes, and the
five-case Linux readiness plan. The target bug failed and the fixed revision
passed both declared target nodes. The full fixed regression then reported
`1153 passed`, `61 skipped`, and two failures, so the case remained unaccepted.

The first failure came from pytest `3.10.1` writing `PYTEST_CURRENT_TEST` into an
`os.environ` object that a 2016 test had replaced with an empty dict. The second
came from `pkg_resources.require('thefuck')`: BugsInPy's frozen requirements
normally create the distribution metadata through an editable VCS install, while
the V4 safety policy intentionally does not install or execute the repository.
Neither failure is excluded or silently accepted. The machine-readable record is
`docs/v4/phase1_linux_reproduction_attempt_2.json`.

## Legacy Harness Adapter

The project profile now materializes a hash-bound support directory in both fixed
SHA checkouts. A pytest `3.10.1`-guarded plugin removes only the later
`PYTEST_CURRENT_TEST` instrumentation. Minimal `thefuck 3.4` metadata and the
three console entry points are reproduced from static `setup.py` declarations,
without executing `setup.py` or installing the project. Every generated file has
an exact content hash and a canonical source-text hash; plugin loading is limited
to modules that resolve inside an approved repository-relative `PYTHONPATH` and
does not follow symlinks.

The two historical failure nodes passed locally under exact Python `3.7.0` and
pytest `3.10.1`. At that checkpoint this was compatibility evidence only; the
candidate remained outside the accepted benchmark. Details are recorded in
`docs/v4/phase1_legacy_harness_adapter_verification.json`.

## Third Attempt

Run `29607565685` on commit
`c9e16e8e16c2babc3c7ef28e597afb18254c99ba` passed every workflow step. The bug
revision failed both target nodes with assertion failures, the fixed revision
passed both target nodes, and the complete fixed regression reported
`1155 passed`, `61 skipped`, and zero failures across 1216 tests. The artifact ZIP
SHA-256 is
`4d7ecce6d11b39ea4953afe126cbf52e34645244f266c825c6277448dbb380fb`;
the internal evidence SHA-256 is
`6fedf804b4154ad702f25a8010c76743513e624fceeb1000b84c36f4efc7491a`.

The V4 acceptance command validated the ZIP member paths and types, artifact,
plan, and evidence hashes, fixed SHAs, exact runtime, preparation-file source
assertions, command argv, non-empty test counts, and all four acceptance booleans.
`bugsinpy-thefuck-16` then became accepted case 21, increasing accepted
repositories from five to six. The updated catalog manifest is
`01cab7473c0ce19b81a37fcf9f030b12809212152188fe3de1652589af40b262`.
Machine-readable evidence is in
`docs/v4/phase1_linux_reproduction_attempt_3.json` and
`docs/v4/phase1_thefuck_16_acceptance_audit.json`.

## Post-Acceptance Replay

Run `29610144866` on commit
`90448197dc066af1f31e2dde09107824250c5000` completed successfully after
`bugsinpy-thefuck-16` had transitioned from `candidate` to `accepted`. This proves
the frozen selection plan remains replayable across the catalog lifecycle change;
accepted cases participate in reproduction planning, while rejected cases remain
excluded. The machine-readable run and artifact record is
`docs/v4/phase1_post_acceptance_replay.json`.

## Evidence

The workflow uploads only the case set, bootstrap plan/result, Linux reproduction
plan, batch summary, and case-level JSON/Markdown evidence. Repository checkouts,
environments, caches, and raw dependency artifacts are excluded. Generalized
artifact names use `v4-phase1-linux-thefuck-<case-set>` and retain evidence for 30
days. The accepted attempt-3 artifact keeps its original immutable name
`v4-phase1-linux-thefuck-16`.
