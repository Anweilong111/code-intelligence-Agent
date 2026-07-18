# V4 Phase 1 HTTPie Linux Reproduction

## Scope

HTTPie contributes five pre-registered BugsInPy candidates in the Phase 1
selection. They are evaluated on Linux with exact Python 3.7.3 and three
case-bound historical dependency variants. The fixed candidate order is:

1. `bugsinpy-httpie-2`
2. `bugsinpy-httpie-1`
3. `bugsinpy-httpie-3`
4. `bugsinpy-httpie-4`
5. `bugsinpy-httpie-5`

The project target is four accepted HTTPie cases. A case is accepted only after
the bug SHA fails the declared targeted test, the fix SHA passes that test, and
the fix SHA passes the applicable full regression command. Environment setup
alone is never counted as a reproduced case.

## Historical Runtime Variants

The source requirement snapshots differ across HTTPie bugs, so one shared
environment would silently substitute dependency versions. Each variant is
bound to its case IDs and original requirements-file SHA-256:

| Variant | Cases | pytest | requests | Requirements SHA-256 |
| --- | --- | ---: | ---: | --- |
| `pytest32-requests200` | 1 | 3.2.1 | 2.0.0 | `b709611f4749c06b107c06a97724177898dbf95904833ea2d00e92ed3e65f704` |
| `pytest54-requests223` | 2, 3 | 5.4.2 | 2.23.0 | `77de192562ac39d2d6a0ad4cbb1000e08c2da9d4ea6b26bd66f5adbd2805740c` |
| `pytest54-requests200` | 4, 5 | 5.4.2 | 2.0.0 | `f504fd7019795d6e32651a492581cef11a48cec8f879f388738dfdc0f747688b` |

The reproduction planner rejects an unknown or multiply mapped case. The
acceptance path also checks that the selected variant SHA matches the catalog's
source requirements SHA, preventing a passing result from a substituted runtime
from entering the benchmark.

## Linux Attempt 1

GitHub Actions run
[`29613751271`](https://github.com/Anweilong111/code-intelligence-Agent/actions/runs/29613751271)
executed commit `8e42f2634dc0b70fd47193d2f71b4fd381d20dac` on Ubuntu 22.04.
The runner successfully provisioned and probed exact Python 3.7.3, created the
first isolated environment, and then stopped during dependency installation.

The failing command retained both `--only-binary=:all:` and `--no-deps`. PyPI
provided wheels for the packages resolved before Blinker, but no wheel exists
for `blinker==1.4`. Pip therefore returned:

```text
No matching distribution found for blinker==1.4
```

This is an environment blocker, not an HTTPie source or test failure. No
targeted test, regression test, or reproduction evidence ran. The accepted-case
count remained 23. The raw artifact is retained outside Git and is identified by
SHA-256 `9d131dd7edf074571994d779d5b88f8aeeccacca46684814965747d43839a0da`.

## Blinker 1.4 Adapter

Relaxing the binary-only policy would allow pip to execute an arbitrary source
build. Instead, all three variants declare the same official PyPI artifact:

- URL: `https://files.pythonhosted.org/packages/1b/51/e2a9f3b757eb802f61dc1f2b09c8c99f6eb01cf06416c0671253536517b6/blinker-1.4.tar.gz`
- Size: `111476` bytes
- SHA-256: `471aee25f3992bd325afa3772f1063dbdbbca947a041b8b89466dc00d606f8b6`
- Source root: `blinker-1.4`
- Selected members: `blinker/` and `blinker.egg-info/`

The adapter validates the HTTPS host, byte size, SHA-256, package name, version,
member count, expanded size, compression ratio, paths, duplicates, file types,
links, and destination containment before writing. It permits only `.py`,
`.pyi`, and a narrow set of direct `.egg-info` metadata files. `PKG-INFO` must
declare exactly `Name: blinker` and `Version: 1.4`.

The real archive audit selected eight files: four Python modules and four
metadata files. `setup.py` exists in the source archive but is outside the
allowlist, is not copied, and is never executed. `blinker==1.4` remains in the
frozen requirements contract, is removed only from the pip install argument
list, and must still appear with the exact version in `pip freeze`.

## Current Gate

The adapter and its safety tests pass locally, but this is not yet a Linux
reproduction result. The next gate is another GitHub Actions run using the
updated profile. It must build all three isolated variants, pass `pip check` and
the frozen-distribution audit, and then produce evidence for all five case IDs.
Only cases satisfying all three reproduction gates may be accepted, in the
pre-registered order above.
