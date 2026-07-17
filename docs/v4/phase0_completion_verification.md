# V4 Phase 0 Completion Verification

## Status

`pass`

Phase 0 has frozen and audited the V4 experiment contract. It has not called a
live model, created the final 50-case benchmark, or produced a V4 repair-rate
claim.

## Frozen Source

- Source commit: `55d799db51c3b4277834b56931fff59b3883adec`
- Branch role: V4 development branch
- V3 baseline: `v3-baseline` -> `43268748cbfb4abb1f54c2e8d41da96e5ba1d92a`
- V4 protocol fingerprint: `3375dda3486f617262a5ec68245ffc5e8f1d7dc37e4f4308f963935fb361395b`
- Protocol audit: 0 errors, 0 warnings, 7 frozen prompts

## Full Regression

The final pytest process explicitly removed all configured model-provider and
GitHub credential variables. Raw values were never read or recorded.

```powershell
python -m pytest -q --basetemp outputs_v4\pytest_v4_phase0_release_green
```

Result: `1426 passed, 2 skipped, 0 failed` in 1236.64 seconds.

The local basetemp is under ignored `outputs_v4/`. This is required because the
release-hygiene tests intentionally create contaminated fixture files; those
fixtures must be audited inside their test snapshot without becoming candidates
in the repository-level release scan.

## Release Hygiene

- Status: `pass`
- Candidate source: `git`
- Candidate files: 544
- Checks: 5/5 pass
- Raw API key findings: 0
- Tool-signature findings: 0
- `outputs_v4/` is ignored and regression tested

The secret scan itself was not weakened. The fix only prevents local V4 runtime
artifacts from entering the Git release candidate set.

## Clean Archive

The source archive was generated from commit `55d799d` and extracted into a
directory without Git metadata.

- Format: zip
- SHA-256: `8d0ee2e782bfded8541a1331bfe8af7a7ac35ff0fda8c1ba8c72cee485f18a34`
- Size: 3,100,473 bytes
- Files: 669
- `.git` present: false
- Top-level outputs directories: 0
- Snapshot hygiene: 5/5 pass using `filesystem_snapshot`
- Release-focused tests: 31 passed in 6.29 seconds

The zip is a local ignored verification artifact under `outputs_v4/`; its hash is
committed here so the tested source can be identified without committing the
archive itself.

## Resolved Findings

1. The V3 architecture document contained a stale statement saying the final 60
   LLM and 60 Hybrid trials had not run. It now records 120/120 completed live
   trials and 423/423 audited RunRecords, and the packaging hash was updated.
2. The first full regression inherited external provider credentials. The final
   release run removes all model and GitHub credentials before pytest, preserving
   the Phase 0 offline boundary.
3. `outputs_v4/` was initially absent from `.gitignore`. A deliberately
   contaminated hygiene fixture then appeared in the repository candidate set.
   The directory is now ignored and covered by a regression assertion.

## Claim Boundary

This verification proves that the V4 protocol, safety constraints, RunRecord
schema, CLI route, tests, release hygiene, and clean source packaging are
reproducible. It does not prove that Full Agent is better than Fixed Workflow.

Phase 1 must create and hash-lock at least 50 reproducible real Python bugs from
at least 15 repositories, split 10/15/25 by repository. Live V4 model calls remain
disabled until the benchmark is locked, a 20-case pilot is ready, and the user
provides new explicit data-disclosure authorization.
