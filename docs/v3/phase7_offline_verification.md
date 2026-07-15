# V3 Phase 7 Offline Verification

## Decision

Phase 7 is **offline-ready but incomplete**. All seven committed Phase 0-6
evidence gates pass, the unified evaluator is deterministic, and the complete
test suite passes. The release status remains `partial` because the required
60 live LLM and 60 live Hybrid repair trials have not completed under the
amended protocol.

The final frozen-protocol preflight returned HTTP 402 in 543 ms before any
repository preparation or repair Trial was submitted. It consumed zero model
tokens, incurred zero measured cost, retained no response content, and produced
zero raw-key findings. Completion therefore requires restored provider
billing/quota access; replacing a Key is not by itself the acceptance condition.
A preflight-only blocker reruns with the same command, while
`--retry-blockers` is required only for historical Trial-level blockers.

This distinction is enforced in code. Missing live values are emitted as JSON
`null`, and `--require-complete` exits nonzero until a structurally valid
120-trial artifact is supplied.

## Verified Offline Evidence

| Dimension | Result | Evidence meaning |
| --- | ---: | --- |
| Offline phase gates | 7/7 pass | Phase 0-6 committed verification artifacts are present and valid. |
| Real bug benchmark | 20 cases, 6 repositories | Independent bug and fix reproduction passed during benchmark construction. |
| Repository startup | 19/20 | Test processes started and terminated under the frozen Phase 2 protocol. |
| Localization Top-1/3/5 | 0.60 / 0.80 / 1.00 | Repository-disjoint difficult localization set. |
| Rule pass@1 | 0/20 | A measured zero, retained rather than hidden or replaced. |
| LLM/Hybrid repair | pending | No live-model repair rate is claimed. |
| Semantic calibration | 2/2 | Human-fix validator calibration only, not Agent repair. |
| Memory evaluation | 0.4286 -> 1.0000 | Controlled structured-memory ablation with authority checks. |
| Security evaluation | 8/8 | Controlled hostile-repository fixtures, not a container guarantee. |

Wilson 95% intervals are included for supported binomial metrics. Protocol
changes make the raw V2 `7/20` and V3 `19/20` repository-start counts
`not_comparable`; no causal uplift is claimed.

## Verification Runs

| Gate | Result | Duration |
| --- | --- | ---: |
| Focused Phase 7 and repair regression | 67 passed | 5.64 s |
| V3, memory, security, and release regression | 189 passed, 2 skipped | 22.62 s |
| Full pytest suite | 1408 passed, 2 skipped | 755.54 s |
| Release hygiene | 5/5 checks, 525 candidate files | current candidate set |

The two skips are explicit Windows-host limitations: this host cannot create
the symbolic-link fixtures used by `tests/test_runtime_security.py` and
`tests/test_v3_semantic_validation.py`. They are reported rather than counted
as passes.

## Live Acceptance Boundary

A complete V3 release requires exactly 120 observed independent trials:

- 20 accepted cases x 3 LLM trials = 60;
- 20 accepted cases x 3 Hybrid trials = 60;
- no missing trial identities;
- complete provider, exact model, Prompt ID, Prompt hash, token, cost, latency,
  outcome, Reflection, and failure-category evidence;
- targeted tests, full regression, and semantic validation retained as
  separate gates.

An artifact marked `pass` with 119 trials is rejected by regression tests. Rule
metrics remain attributed to Rule, and LLM/Hybrid metrics cannot inherit Rule,
V2 fixture, or human-calibration values.

## Reproduction

```powershell
python -m code_intelligence_agent v3-release-eval `
  outputs_v3/phase7_release `
  --root . `
  --require-offline-pass
```

After a valid live artifact exists:

```powershell
python -m code_intelligence_agent v3-release-eval `
  outputs_v3/phase7_release `
  --root . `
  --live-evaluation outputs_v3/phase3_live/evaluation.json `
  --require-complete
```

The complete-release command must remain nonzero while live evidence is absent,
incomplete, or invalid.
