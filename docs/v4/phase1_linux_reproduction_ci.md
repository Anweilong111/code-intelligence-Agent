# V4 Phase 1 Linux Reproduction CI

## Purpose

The first real Thefuck candidate proved its targeted bug/fix behavior on Windows,
but the historical full regression suite contains POSIX-only assumptions. V4 does
not exclude those tests or count the case as reproduced. The Linux CI lane runs
the same fixed SHAs and three acceptance gates on the required platform.

The workflow is defined in
`.github/workflows/v4-phase1-linux-reproduction.yml`. Automatic runs are limited
to the V4 branch and the workflow, bootstrapper, fixed catalog, selection plan,
and reproduction profile paths. It can also be started manually.

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
benchmark candidate and is not a general permission to execute arbitrary GitHub
repositories.

## Acceptance Contract

The job must satisfy all of the following:

1. Miniconda reports exact Python `3.7.0` from a non-symlink executable.
2. The isolated runtime bootstrap result is `pass`.
3. All five Thefuck candidates are `ready` on Linux with zero planning blockers.
4. The bug SHA fails the declared targeted tests.
5. The fix SHA passes the same targeted tests.
6. The fix SHA passes the full declared regression command.
7. The reproduction CLI exits successfully under `--require-pass`.

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

## Evidence

The workflow uploads only the bootstrap plan/result, Linux reproduction plan, and
case-level JSON/Markdown evidence. Repository checkouts, environments, caches, and
raw dependency artifacts are excluded. The artifact name is
`v4-phase1-linux-thefuck-16` and its retention period is 30 days.
