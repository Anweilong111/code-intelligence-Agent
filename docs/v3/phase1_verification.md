# V3 Phase 1 Verification

- Status: `pass`
- Verified: `2026-07-15`
- Source: BugsInPy `11c5f1eea954a42132cfd06bf257766a7963e0fd`
- Catalog: `20 accepted / 5 rejected / 0 unresolved`
- Environment profiles: `8`, binding `25/25` catalog cases
- Focused tests: `27 passed`
- Full regression: `1271 passed in 1231.14s`
- Release hygiene: `5/5 pass`

## Acceptance Evidence

Every accepted case has a checksummed ignored reproduction artifact that matches
the current case ID, bug/fix SHA, exact Python patch version, test overlay, and
command arguments. Acceptance requires a real bug-target failure, fix-target
pass, and fixed full-regression pass, all with non-zero test counts.

The committed catalog contains only portable summaries. Raw test output,
checkouts, runtime directories, local absolute paths, and gold patches are not
committed or exposed to execution.

## Integrity Finding

A fresh-checkout rerun invalidated an earlier PySnooper 2 result because the old
directory contained test helper files that were not declared by BugsInPy's
single-file metadata. The stale result was discarded. Three test-only helper
files are now explicit, reasoned, checksummed overlays from the fix SHA, and the
case passes all three gates from a clean checkout. No acceptance gate was
weakened.

## Boundary

This verification establishes the Phase 1 benchmark and oracle. Live model
trials, Rule/LLM/Hybrid repair rates, pass@1/pass@3, token cost, latency, and
reflection recovery remain Phase 3 deliverables.
