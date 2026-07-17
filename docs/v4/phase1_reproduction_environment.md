# V4 Phase 1 Reproduction Environment

## Purpose

The environment bootstrapper turns an exact, read-only base Python interpreter
into a project-isolated runtime without executing repository installation hooks.
It is intentionally separate from the reproduction planner: planning is offline,
while dependency installation requires an explicit operator authorization.

The top-level command is `v4-bootstrap-runtime`. A generated plan freezes the
base interpreter, target environment, exact package versions, required import
probes, allowed dependency sources, and a canonical SHA-256 fingerprint. The run
command rejects a modified plan before creating or changing an environment.

## Safety Contract

The bootstrapper enforces the following rules:

1. The base interpreter must match the requested Python patch version.
2. The target environment must remain below the configured isolated runtime root.
3. PyPI dependencies must use one exact `==` version and binary wheels only;
   `--no-deps` prevents unregistered transitive resolution.
4. Direct URLs, VCS requirements, extras, markers, editable installs, and local
   repository installation are rejected.
5. Repository `setup.py`, `setup.sh`, and build hooks are never executed.
6. Network access is available only after authorization and may use only an
   unauthenticated loopback HTTP(S) proxy.
7. The shared base runtime is never modified.
8. `pip check` must report no broken requirements, and `pip freeze` must match
   every distribution frozen by the plan.
9. The final runtime must pass both exact-version and required-module probes.

An existing valid environment can be incrementally completed. Environment
creation is skipped, all frozen dependencies are rechecked idempotently, and the
full module probe runs again. A partial directory without its target interpreter
is not automatically deleted or reused.

## Hash-Pinned Pure-Python Archive Adapter

`win-unicode-console==0.5` has no wheel on PyPI. Its official PyPI metadata
publishes one source ZIP and explicitly documents manual installation by copying
`win_unicode_console/` and `run.py` into `site-packages`. Allowing a normal source
install would execute package build code, so V4 uses a narrower adapter.

The adapter accepts only a frozen `files.pythonhosted.org` HTTPS URL, exact byte
size, SHA-256, source root, and member allowlist. Before writing any package file,
it rejects encrypted members, symbolic links, duplicate or escaping paths,
excessive expanded size or compression ratio, non-Python selected files, and
conflicts with existing site-packages files. It copies only `.py` or `.pyi`
members under the allowlist and records the SHA-256 and disposition of every
installed file. The archive's `setup.py` remains unselected and unexecuted.

Frozen official artifact:

- URL: `https://files.pythonhosted.org/packages/89/8d/7aad74930380c8972ab282304a2ff45f3d4927108bb6693cabcc9fc6a099/win_unicode_console-0.5.zip`
- Size: `31420` bytes
- SHA-256: `d4142d4d56d46f449d6f00536a73625a871cba040f0bc1a2e305a04578f07d1e`
- Installed allowlist: `win_unicode_console/`, `run.py`
- Installed files: 12

## Real Thefuck Result

The isolated runtime reached `status=pass` with exact Python `3.7.0` and all
eight required modules available. The bootstrap result fingerprint is
`636d60a7cda0262e42f840400b0e686e0e4a3a33c9e9bd593e58d865b5b0987c`.
All 20 planned distributions matched their exact versions, and `pip check`
reported no broken requirements.
No repository setup script, repository project install, shared-runtime mutation,
or model call occurred.

Case `bugsinpy-thefuck-16` then passed the first two reproduction gates:

- bug SHA targeted tests failed as expected;
- fix SHA targeted tests passed;
- fix SHA full regression did not pass on Windows.

The fix regression collected 1,216 tests: 1,146 passed, 61 skipped, and 9 failed.
The failures include POSIX-only assumptions such as constructing `PosixPath` on
Windows and comparing `/rules/...` paths with Windows path separators. They are
not accepted as patch regressions, but they also cannot be ignored to manufacture
a passing benchmark case. The case therefore remains unaccepted.

The profile now freezes `required_execution_platform=linux`. On this Windows host,
all five Thefuck development candidates are blocked before checkout with
`execution_platform_mismatch:required_linux:observed_windows`. The corresponding
plan fingerprint is
`6b148be6c882adfeb15fa822397bf3cfa439303e83648c14edd3286f28ded6b7`.

## Commands

```powershell
python -m code_intelligence_agent v4-bootstrap-runtime plan `
  datasets\v4_agent_effectiveness\reproduction_profiles.json `
  thefuck 3.7.0 `
  outputs_v4\thefuck_py370_bootstrap_plan.json `
  --base-runtime-root outputs_v3\runtimes `
  --isolated-runtime-root outputs_v4\runtimes
```

Execution requires a separate explicit authorization:

```powershell
python -m code_intelligence_agent v4-bootstrap-runtime run `
  outputs_v4\thefuck_py370_bootstrap_plan.json `
  outputs_v4\thefuck_py370_bootstrap_result.json `
  --authorize-dependency-install `
  --proxy http://127.0.0.1:7897 `
  --require-pass
```

## Next Gate

The next valid step for Thefuck is a Linux CI or container reproduction using the
same fixed SHAs, dependency contract, and three acceptance gates. The Windows
result is retained as auditable platform-blocker evidence and is not counted among
the 50 accepted V4 benchmark cases.
