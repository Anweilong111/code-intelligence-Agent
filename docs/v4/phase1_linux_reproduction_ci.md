# V4 Phase 1 Linux Reproduction CI

## Purpose

The first real Thefuck candidate proved its targeted bug/fix behavior on Windows,
but the historical full regression suite contains POSIX-only assumptions. V4 does
not exclude those tests or count the case as reproduced. The Linux CI lane runs
the same fixed SHAs and three acceptance gates on the required platform.

The workflow is defined in
`.github/workflows/v4-phase1-linux-reproduction.yml`. Its first automatic run is
limited to the V4 branch and to changes in that workflow file. It can also be
started manually after the initial checkpoint.

## Runtime Construction

The runner is frozen to `ubuntu-22.04`. The host Python only runs the V4
orchestrator. The target repository tests use a separate exact Python `3.7.0`
runtime created from the runner's preinstalled Miniconda and the conda-forge full
repodata. The V4 bootstrapper then creates a copied project-isolated environment,
installs only exact binary-wheel requirements, executes `pip check`, audits every
frozen distribution with `pip freeze`, and probes the required modules.

Runtime profiles now support both Windows and Linux executable mappings. Platform
specific dependencies are also explicit: `win_unicode_console` and its hash-pinned
manual archive apply only on Windows and are absent from the Linux plan.

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

## Evidence

The workflow uploads only the bootstrap plan/result, Linux reproduction plan, and
case-level JSON/Markdown evidence. Repository checkouts, environments, caches, and
raw dependency artifacts are excluded. The artifact name is
`v4-phase1-linux-thefuck-16` and its retention period is 30 days.
