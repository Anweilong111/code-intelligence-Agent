# V3 Phase 1 Verification

- Status: `pass`
- Verified: `2026-07-15`
- Source: BugsInPy `11c5f1eea954a42132cfd06bf257766a7963e0fd`
- Catalog: `20 accepted / 5 rejected / 0 unresolved`
- Environment profiles: `8`, binding `25/25` catalog cases
- Focused tests: `28 passed`
- Full regression: `1314 passed in 782.79s`
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
single-file metadata. Black 2 and Black 5 exposed the same benchmark-integrity
class through missing formatter fixtures. The stale evidence was discarded and
all required test-only files are now explicit, reasoned, checksummed overlays
from the fix SHA.

Reproduction now rejects a bug-side `FileNotFoundError` as a benchmark-input
blocker when the missing test path exists at the fix revision but is absent from
the declared overlay. All three cases pass the unchanged failure/pass/regression
gates from clean checkouts; no acceptance gate was weakened.

## Boundary

This verification establishes the Phase 1 benchmark and oracle. Live model
trials, Rule/LLM/Hybrid repair rates, pass@1/pass@3, token cost, latency, and
reflection recovery remain Phase 3 deliverables.
