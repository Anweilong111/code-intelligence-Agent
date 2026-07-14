# V3 Phase 2 Verification

- Status: `pass`
- Verified: `2026-07-15`
- Runtime: `CPython 3.13.12`
- Fixed-SHA repositories: `20`
- Structured Agent reports: `20/20`
- Test commands discovered: `20/20`
- Isolated test processes started and explicitly terminated: `19/20`
- Required threshold: `>=14/20`
- Repository code/dependency installs requested: `0/0`
- Focused and integration tests: `299 passed in 188.55s`
- Full regression: `1280 passed in 667.90s`
- Release hygiene: `5/5 pass`

## Acceptance Evidence

The strict Phase 2 evaluator passed all 14 checks. Every started process used the
pytest executable prepared in its repository-specific venv, and every process
returned an explicit pass, fail, or timeout outcome. No started case fell back to
the host interpreter.

Nineteen repositories satisfied the startup contract. Typer was deliberately
skipped because the current Python 3.13.12 runtime violates the fixed revision's
Python constraint. Its setup plan reports `python_version_incompatible`, and its
execution plan reports `runner_probe_setup_not_ready`.

## Integrity Findings

The first strict evaluation exposed two audit defects rather than being accepted
at face value:

1. Typer's failed isolated setup was followed by a host-interpreter test run.
   The execution planner now makes an unready runner probe non-executable.
2. Polars reported a missing native binary but was classified as a generic
   command failure. The execution classifier now emits
   `missing_native_extension`.

After both fixes, the two cases were rerun, the 20 structured reports were
reaggregated, and the strict evaluator passed. The evaluator now also prioritizes
the startup-stage compatibility blocker over a stale controller-level timeout
when a process was not started.

## Comparison Boundary

The V2 paired baseline is 7/20 and the V3 result is 19/20, a difference of 12
cases or 0.6000 in rate. This is not an unchanged-protocol algorithm uplift: V2
authorized test checkout for 7 cases, whereas V3 authorizes all 20 and uses the
new isolated pytest runner probe.

The result proves safe test-process startup and failure classification. It does
not prove that 19 repositories pass their suites, that 19 defects were localized,
or that any patch was repaired. Real Rule/LLM/Hybrid repair evaluation remains
Phase 3.
