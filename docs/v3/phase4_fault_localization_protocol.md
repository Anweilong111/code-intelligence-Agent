# V3 Phase 4 Real-Bug Fault Localization Protocol

## Objective

Phase 4 measures whether the Agent can rank the real bug-side function before it
sees the fix. It does not measure patch generation or repair success. The phase
combines static rules, program structure, actual failing-test execution,
traceback evidence, deterministic lexical semantics, complexity, and change
history into an auditable function ranking.

The protocol has four non-negotiable properties:

1. Candidate scope and raw signals are frozen before fix-side oracle resolution.
2. FinalScore weights are selected on validation repositories only.
3. The selected profile is hashed and frozen before test metrics are computed.
4. Every stored Top-k score can be reconstructed from raw signals and weights.

## Dataset And Split Isolation

The evaluation uses the 20 accepted fixed-SHA BugsInPy cases from Phase 1.

| Split | Repositories | Cases | Use |
| --- | --- | ---: | --- |
| development | psf/black, cool-RR/PySnooper | 7 | diagnostics only |
| validation | tiangolo/fastapi, tqdm/tqdm | 8 | weight selection |
| test | ytdl-org/youtube-dl | 5 | one frozen evaluation |

Repository sets do not overlap. Of eight validation cases, seven have at least
one changed function that already exists in the bug revision. `tqdm-3` adds a
new method and therefore cannot have a bug-side function rank; it remains in
file metrics and is explicitly excluded from function metrics.

The test split is repository-disjoint but contains only one repository. Results
are valid for this frozen split, but they are not a broad confidence interval
over arbitrary Python repositories.

## Execution Order And Leakage Prevention

For each case, the evaluator performs these steps in order:

1. Verify the accepted reproduction artifact, bug SHA, overlay hashes, and
   pinned Python runtime.
2. Convert the validated failing execution into failing-test and traceback
   evidence.
3. Select a bounded analysis scope from test paths, traceback paths, imports,
   reverse imports, and lexical path matches. Ground-truth fields are not read.
4. Parse the bug checkout and build AST, Call Graph, and Program Graph models.
5. Execute the manifest-pinned targeted test under the case-specific Python
   interpreter while collecting real line and call events with `sys.settrace`.
6. Compute all candidate signals and persist a raw signal matrix with
   `ground_truth_included=false`.
7. Hash that frozen ranking snapshot.
8. Only then compare bug and fix source to resolve the oracle. The oracle stores
   the same snapshot hash and states `ground_truth_used_for_ranking=false`.
9. Search weights using validation cases only, hash the selected profile, and
   evaluate development, validation, and test without changing that profile.

## Exact Ground-Truth Construction

Catalog function labels can be class-level or stale, so they are not used as an
exact function oracle. The resolver instead uses the actual bug and fix source:

1. Normalize the catalog's production source-file paths.
2. Use `difflib.SequenceMatcher` to identify changed bug-side and fix-side line
   numbers for replace, delete, and insert operations.
3. Parse both revisions with Python AST.
4. Build spans for functions, methods, async functions, and nested functions.
   Decorator lines are included in the effective span.
5. Map each changed line to the smallest containing span, which selects the
   innermost changed function instead of its enclosing method or class.
6. Keep bug-side changed functions directly. Project a fix-side changed
   function only when the same qualified function exists in the bug revision.
7. If a fix only introduces a new function, mark the case function-unrankable
   rather than inventing a bug-side target.

This process corrected coarse labels such as a class-level target and captures
decorator-only changes as changes to the decorated function.

## Runtime Evidence

The coverage runner supports exact commands in the form
`python -m <module> ...`, including both pytest and unittest. A tracer script is
launched with the case-pinned historical interpreter, sets repository and test
arguments, and records production line, call, return, and exception events.

The evaluator reports native line/call events. Branch and path features are
inferred from those events and AST structure; they are not presented as native
branch coverage. A targeted test is considered failing runtime evidence only
when its real return code is non-zero.

When runtime execution is available, its execution IDs are authoritative for
the SBFL failed/passed-test denominator. Manifest-derived baseline labels remain
available as graph evidence after a real failure is observed, but they are not
counted as a second execution of the same test. This prevents an equivalent
baseline/runtime pair from reducing Ochiai suspiciousness through duplicate
denominator entries.

All 20 accepted cases executed one targeted command, failed as expected, and
covered at least one production function.

## Signal Definitions

Each candidate function receives normalized signals in `[0, 1]`:

| Signal | Meaning |
| --- | --- |
| Static | Combined confidence of deterministic bug-rule findings |
| Graph | Structural relevance from calls, data/control dependencies, centrality, and failure proximity |
| SBFL | Ochiai-style suspiciousness from failing/passing execution coverage |
| TestFailure | Proximity to dynamically identified failing-test nodes |
| Traceback | Direct or decayed proximity to real traceback functions |
| Semantic | Deterministic token similarity between failure evidence and function context |
| Complexity | Normalized cyclomatic-complexity prior |
| ChangeHistory | Normalized Git change-history prior when available |
| LLM | Optional model scorer; zero and unavailable in this experiment |
| Risk | Patch-risk penalty derived from structural properties |

Semantic is lexical and deterministic. It must not be called an LLM signal.

## FinalScore And Attribution

For function `f`, the score is:

```text
FinalScore(f) = clamp(
    w_sbfl * SBFL(f)
  + w_graph * Graph(f)
  + w_static * Static(f)
  + w_semantic * Semantic(f)
  + w_llm * LLM(f)
  + w_test * TestFailure(f)
  + w_trace * Traceback(f)
  + w_complexity * Complexity(f)
  + w_history * ChangeHistory(f)
  - w_risk * Risk(f),
  0, 1)
```

The selected validation profile is `simplex-021`:

| Component | Weight |
| --- | ---: |
| SBFL | 0.225 |
| TestFailure | 0.175 |
| Traceback | 0.100 |
| Semantic | 0.250 |
| Complexity | 0.125 |
| ChangeHistory | 0.125 |
| Static / Graph / LLM / Risk | 0.000 |

The profile SHA-256 is
`5e86aa8ffd55c93f9f862f5698376edee0ce8144ff0ce7d06fe170394b79e10a`.

For every stored Top-k row, the artifact includes raw signals, active weights,
each signed contribution, the pre-clamp sum, clamp adjustment, reconstructed
score, and reconstruction error. The Phase 4 audit reconstructed all 20 case
artifacts without error.

## Validation-Only Weight Search

The search creates 141 deterministic profiles. It places a coarse simplex over
five signal families: Static, Graph, Dynamic, Semantic, and Auxiliary. Dynamic
weight is split into SBFL/TestFailure/Traceback at `45%/35%/20%`; Auxiliary is
split equally between Complexity and ChangeHistory. Risk is searched at `0` and
`0.05`; LLM remains zero.

The optimization objective is:

```text
0.25 * MAP
+ 0.25 * MRR
+ 0.20 * nDCG@3
+ 0.15 * Top1
+ 0.10 * Top3
+ 0.05 * (1 - EXAM)
```

Selection first maximizes the minimum of the global validation objective and
per-repository validation objectives. Remaining ties use global objective, MAP,
MRR, Top-1, profile sparsity, and stable profile name. The function rejects any
non-validation case input.

## Metrics And Denominators

Function metrics include Top-1/3/5, MRR, MAP, nDCG@3, and EXAM over the 19
function-rankable cases. File metrics deduplicate ranked functions by file and
retain all 20 cases. A missing relevant function receives reciprocal rank zero,
average precision zero, and EXAM one.

Variants are Rule-only, Graph-only, Dynamic-only, Semantic-only, Fusion,
without-Rule, without-Graph, without-Dynamic, without-Semantic, and
without-Auxiliary. LLM-only is `not_applicable` because no real localization
scorer was configured.

## Frozen Test Results

| Variant | Top-1 | Top-3 | Top-5 | MRR | MAP | EXAM |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Rule-only | 0.0000 | 0.0000 | 0.0000 | 0.0190 | 0.0192 | 0.4699 |
| Graph-only | 0.0000 | 0.0000 | 0.2000 | 0.0750 | 0.0749 | 0.1011 |
| Dynamic-only | 0.0000 | 0.0000 | 0.4000 | 0.1601 | 0.1442 | 0.0154 |
| Semantic-only | 0.2000 | 0.2000 | 0.4000 | 0.2803 | 0.1945 | 0.0826 |
| Fusion | 0.6000 | 0.8000 | 1.0000 | 0.7067 | 0.6144 | 0.0036 |

Removing Dynamic reduces Top-1 by `0.4000`, MRR by `0.4238`, and MAP by
`0.3283`. Removing Semantic reduces Top-1 by `0.2000`, and removing Auxiliary
reduces Top-1 by `0.2000`. Rule and Graph have zero selected weight, so their
removal cannot change Fusion; this experiment does not claim they improved the
frozen test result.

The two Fusion Top-1 misses appear at ranks 3 and 5; all five test cases are in
Top-5. The test split contains no multi-file case, so multi-file conclusions
come from development/validation diagnostics rather than frozen test evidence.

## Reproduction

```powershell
python -m code_intelligence_agent v3-localization-eval outputs_v3/localization_phase4 `
  --coverage-timeout 180 `
  --release-docs-dir docs/v3
```

Committed evidence:

- `docs/v3/phase4_localization_metrics.json`
- `docs/v3/phase4_difficult_localization.md`
- `docs/v3/phase4_test_top5_attribution.json`

The local full artifact is ignored because it contains large per-case matrices
and runtime paths. Its SHA-256 is recorded in the committed metrics file.
