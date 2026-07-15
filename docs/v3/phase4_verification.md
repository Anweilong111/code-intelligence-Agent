# V3 Phase 4 Verification

- Overall status: `pass`
- Verified: `2026-07-15`
- Accepted / ready real bugs: `20/20`
- Real failing runtime coverage: `20/20`
- Ground-truth resolution: `20/20`
- Score reconstruction: `20/20`
- Repository-disjoint splits: `pass`
- Test ground truth used during weight search: `false`

## Frozen Test Result

The evaluator searched 141 profiles on seven function-rankable validation cases,
froze `simplex-021`, and evaluated five repository-disjoint test cases once.

| Metric | Fusion |
| --- | ---: |
| Top-1 | 0.6000 |
| Top-3 | 0.8000 |
| Top-5 | 1.0000 |
| MRR | 0.706667 |
| MAP | 0.614359 |
| nDCG@3 | 0.622629 |
| EXAM | 0.003639 |

All five test cases appear in the Top-5. The two Top-1 misses appear at ranks 3
and 5. Dynamic, deterministic Semantic, and Auxiliary signals have positive
frozen-test ablation contributions. Rule and Graph received zero validation
weight, so no Fusion gain is attributed to them.

## Integrity Gates

| Gate | Result |
| --- | ---: |
| Raw signal matrices exclude ground truth | 20/20 |
| Oracle snapshot hash matches frozen ranking | 20/20 |
| Exact diff-to-AST ground truth resolved | 20/20 |
| Real failing tests cover production functions | 20/20 |
| Runtime/baseline SBFL failure IDs are not double-counted | pass |
| Stored Top-k scores reconstruct exactly | 20/20 |
| Split repository overlap | 0 |
| Committed Phase 4 absolute local paths | 0 |
| Raw API keys | 0 |
| Release artifact line endings | stable LF |
| Release hygiene | 5/5 pass |

`tqdm-3` is intentionally function-unrankable because the fix introduces a new
method that does not exist in the bug revision. It remains in the file-level
denominator instead of being converted into an artificial function target.

## Test Verification

| Suite | Result |
| --- | --- |
| Phase 4 and localization regression | 80 passed in 12.12s |
| Full repository pytest | 1328 passed in 775.50s |
| Warm-cache Phase 4 reproduction | 20 cases in 5.4167s |

Signal extraction protocol `v3_localization_signals_1.3.0` makes real runtime
execution IDs authoritative for the SBFL denominator while retaining validated
baseline failure labels only as graph evidence. The cache fingerprint covers
the bug SHA, targeted commands, test
environment, reproduction artifact, runtime, coverage mode and timeout, bug
Python-source fingerprint, and signal-extraction version. Corrupt or mismatched
cache entries are recomputed.

## Evidence

- `docs/v3/phase4_fault_localization_protocol.md`
- `docs/v3/phase4_difficult_localization.md`
- `docs/v3/phase4_localization_metrics.json`
- `docs/v3/phase4_test_top5_attribution.json`
- `docs/v3/phase4_verification.json`

The committed Top-5 attribution artifact contains portable function IDs, raw
signals, selected weights, signed contributions, clamp adjustments, and score
reconstruction errors. The large local per-case matrices remain ignored.

## Boundary

Phase 4 verifies real-bug localization, not real-model patch success. LLM-only
localization is `not_applicable`, and deterministic Semantic-only results are
not presented as model results. The 120 paid LLM/Hybrid repair trials remain
pending until a fresh API key is injected through the current process
environment.
