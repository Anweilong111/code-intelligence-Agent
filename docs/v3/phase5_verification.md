# V3 Phase 5 Verification

## Result

Phase 5 passes its implementation, calibration, regression, and release-hygiene
gates. A repair can no longer reach `verified_repair` after targeted and full
regression alone. It must also receive a complete semantic pass.

The trial implementation now retries transient operating-system locks while
cleaning disposable reverse-mutation workspaces. Exhausted cleanup emits a
runtime warning instead of terminating the paid batch. This changes the trial
implementation fingerprint but not the semantic acceptance contract.

## Implemented Gate

Six checks are required for the current benchmark cases:

1. AST-derived API/type/decorator contract compatibility.
2. Static semantic-diff risk checks.
3. Candidate-to-workspace and cross-file removed-symbol consistency.
4. Independent changed-line minimality.
5. Bug-fail versus patch-pass target differential execution.
6. Per-edit reverse mutation killed by the targeted or full test oracle.

Generated boundary/property probes and benchmark-authored semantic commands are
required only when applicable/configured. Their absence is preserved as
`not_applicable`; it is not reported as a passed generated test.

Required failure maps to a semantic-layer failure. A blocker or incomplete
required oracle maps to `unverified_suggestion`. The experiment record validator
also rejects a semantic pass without detailed evidence and
`claim_eligible=true`.

## Human-Fix Calibration

| Case | Source edits | Semantic | Reverse mutations |
| --- | ---: | --- | ---: |
| `bugsinpy-pysnooper-1` | 2 | pass | 2/2 killed |
| `bugsinpy-pysnooper-3` | 1 | pass | 1/1 killed |

Summary:

- Known-correct human fixes accepted: `2/2`
- False rejections: `0`
- Blockers: `0`
- Reverse mutations killed: `3/3`
- Generated boundary probes: `0` applicable
- Manifest semantic commands: `0` configured

The fix-side source was used only after generation to calibrate validator false
rejection. It was never available to localization, planning, candidate
generation, reflection, or an LLM. These two results are not Agent repair
successes and do not enter pass@1/pass@3.

## Test Evidence

| Gate | Result | Duration |
| --- | --- | ---: |
| Phase 5 + repair regression | 56 passed, 1 skipped | 10.86 s |
| All V3 tests | 135 passed, 1 skipped | 61.21 s |
| Full pytest suite | 1410 passed, 2 skipped | 931.90 s |
| Release hygiene | 5/5 checks, 525 candidate files | n/a |

The two full-suite skips attempt to create symbolic links on Windows; the host
did not grant those operations. Parent traversal rejection and runtime
workspace symlink scans remain deterministic, and the skipped results are
retained rather than reported as passes.

The release audit found no raw API keys, tracked local outputs, binary documents,
tool-signature traces, or documentation that grants an LLM judge authority over
pytest/sandbox execution. Committed Phase 5 evidence contains no local absolute
paths and is LF-normalized.

## Claim Boundaries

- The calibration has only two cases from one development repository.
- `3/3` is reverse-edit mutation sensitivity, not general mutation coverage.
- No generated boundary/property probe applied to these calibration cases.
- Cross-file import analysis is static and does not resolve arbitrary dynamic
  imports or exports.
- API compatibility is intentionally strict and may reject valid refactors.
- Live Rule/LLM/Hybrid repair rates are claimed only from a separate complete
  120-trial artifact that passes the Phase 7 release audit.

See the [semantic validation protocol](phase5_semantic_validation_protocol.md)
and [machine-readable calibration](phase5_semantic_calibration.json) for the
exact algorithms, per-check states, and compact test outcomes.
