# Phase 4 Evidence-Fused Fault Localization

## Result

Phase 4 replaces the V1 mixed graph score with an explicit evidence contract for
function-level fault localization. The default `evidence_v2` profile separates
static findings, structural graph priors, executed failing-test evidence,
dynamically parsed stack frames, coverage/SBFL, semantic similarity, bounded LLM
evidence, complexity, Git change history, and patch risk. Every ranked function
contains its raw signals, active weights, contribution terms, availability flags,
and a score reconstruction field.

The repository-disjoint evaluation selected weights on 15 CPython validation
cases, froze the selected profile, and then evaluated 10 TheAlgorithms cases and
10 blind Click/Pluggy cases. Evidence V2 matched the V1 ranking metrics on all
three splits, so the non-regression gate passed.

The focused regression contains 127 passing tests in 89.70 seconds. The complete
repository regression contains 1183 passing tests, zero failures, and completed
in 649.00 seconds on the recorded development environment.

## Evidence Contract

The localizer distinguishes evidence from priors:

| Signal | Source | Availability rule |
| --- | --- | --- |
| `StaticRuleScore` | AST/rule findings attached to a function | Available only when a detector emitted a finding |
| `GraphScore` | Program Graph structure | Structural prior; does not claim a test failed |
| `TestFailureScore` | Executed failing test IDs plus coverage/call reachability | Zero unless `dynamic_evidence_test_ids` is non-empty |
| `StackTraceScore` | Dynamically parsed production stack frames | Zero unless `dynamic_traceback_function_ids` is non-empty |
| `SBFLScore` | Per-test coverage and pass/fail labels | Zero without failing tests and coverage |
| `SemanticScore` | Failure/test tokens compared with function tokens | Zero without a semantic query overlap |
| `LLMScore` | Optional scorer output | Zero without a configured scorer; gated by program evidence |
| `ComplexityScore` | Function AST | Static risk prior, not proof of a fault |
| `ChangeHistoryScore` | Bounded `git blame --line-porcelain` analysis | Zero/skipped when Git evidence is unavailable |
| `RiskScore` | Normalized production caller in-degree | Subtracted as a patch-risk penalty |

The old `traceback_function_ids` field is retained for V1 compatibility because
older coverage/manifest fallbacks can populate it. Evidence V2 trusts only the
new `dynamic_traceback_function_ids` field for `StackTraceScore`. This prevents
coverage-derived candidates from being presented as real stack frames.

## Score Definitions

### StaticRuleScore

For finding confidences `c_1 ... c_n` attached to a function:

```text
StaticRuleScore = 1 - product(1 - c_i)
```

This probabilistic union keeps the value in `[0, 1]` and lets multiple
independent findings increase suspicion without an unbounded sum.

### Coverage/SBFL Score

The function-level Ochiai score is:

```text
SBFLScore(f) = failed_covered(f)
             / sqrt(total_failed * (failed_covered(f) + passed_covered(f)))
```

Statement, branch, and path variants apply the same Ochiai calculation to their
finest available coverage element and retain the maximum suspicious element for
the function. If there are no failing tests or no covered element, the score is
zero.

### Structural GraphScore

Each graph feature is normalized against the maximum value in the current
candidate set. Evidence V2 computes:

```text
GraphScore = clamp(
    0.18 * DataDependency
  + 0.18 * ControlFlow
  + 0.14 * Centrality
  + 0.14 * PageRank
  + 0.16 * CallerImpact
  + 0.10 * ModuleDependency
  + 0.10 * AsyncCall
)
```

Unlike the V1 graph score, this term excludes traceback, test coverage, dynamic
test evidence, static findings, and patch risk. Those signals now have separate
contributions and can be ablated independently.

### TestFailureScore And StackTraceScore

An exact dynamic hit receives `1.0`. A reachable neighbor receives bounded graph
propagation:

```text
propagated(d) = decay ** max(0, d - 1)
decay = 0.5
maximum depth = 3
```

Nodes beyond depth three receive zero. `TestFailureScore` starts only from tests
that were actually executed and failed. `StackTraceScore` starts only from
dynamically parsed production frames. This keeps a graph prior from being
misreported as runtime evidence.

### SemanticScore

Failure messages, failing test names, and related test metadata form query token
set `Q`. Function name, qualified name, file stem, source, and finding metadata
form document token set `D`. CamelCase and snake_case are split and stop words
are removed.

```text
SemanticScore = |Q intersection D| / sqrt(|Q| * |D|)
```

### ComplexityScore

Cyclomatic complexity starts at one and adds branches for `if`, loops,
comprehensions, boolean alternatives, exception handlers, `else` branches, and
match cases. It is normalized within the current repository:

```text
excess(f) = max(0, complexity(f) - 1)
ComplexityScore(f) = log1p(excess(f)) / log1p(max_excess)
```

The logarithm limits domination by a single unusually large function.

### ChangeHistoryScore

For each function, bounded Git blame evidence computes unique last-change
commits, commit density over the function line range, and recency. Recency uses a
180-day half-life:

```text
recency = exp(-ln(2) * age_days / 180)

ChangeHistoryScore = clamp(
    0.50 * normalized_log_commit_diversity
  + 0.30 * normalized_commit_density
  + 0.20 * recency
)
```

The analyzer uses list-form subprocess arguments, a five-second command timeout,
and a default 200-file cap. Missing Git, a non-Git input, command errors, and
partial analysis are reported explicitly rather than converted into synthetic
history.

### LLMScore Gate

The optional scorer output is clamped to `[0, 1]`. With the V2 profile, it is
discarded unless at least one fault-specific program signal is positive:
`StaticRuleScore`, `SBFLScore`, `TestFailureScore`, `StackTraceScore`, or
`SemanticScore`. Structural graph, complexity, and change history alone cannot
unlock an LLM contribution.

The weighted LLM contribution is capped at `0.10`:

```text
effective_llm = min(raw_llm, 0.10 / llm_weight)
```

The selected profile uses `llm_weight = 0.05`, so a normal `[0, 1]` scorer can
contribute at most `0.05`; the larger cap protects alternative profiles.

## FinalScore And Attribution

For every candidate function:

```text
FinalScore = clamp(
    w_sbfl        * SBFLScore
  + w_graph       * GraphScore
  + w_static      * StaticRuleScore
  + w_semantic    * SemanticScore
  + w_llm         * effective_LLMScore
  + w_test        * TestFailureScore
  + w_traceback   * StackTraceScore
  + w_complexity  * ComplexityScore
  + w_history     * ChangeHistoryScore
  - w_risk        * RiskScore
)
```

The selected coverage-aware weights are:

| SBFL | Graph | Static | Semantic | LLM | Test failure | Traceback | Complexity | History | Risk penalty |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.22 | 0.18 | 0.15 | 0.05 | 0.05 | 0.15 | 0.10 | 0.05 | 0.05 | 0.05 |

When coverage is unavailable, SBFL, TestFailure, and StackTrace weights are zero;
the static-only profile uses Graph `0.25`, Static `0.45`, Semantic `0.10`, LLM
`0.05`, Complexity `0.10`, History `0.05`, and Risk penalty `0.05`.

Every result stores `contribution_<signal>`, `weight_<signal>`,
`score_reconstruction`, and `contribution_clamp_adjustment`. The report can
therefore explain both why one function ranks highly and whether the final clamp
changed the raw weighted sum.

## Split Protocol

The protocol in `datasets/localization_v2/split_protocol.json` is repository
disjoint:

| Split | Repository group | Cases | Purpose |
| --- | --- | ---: | --- |
| Validation | `python/cpython` | 15 | Search among four V2 weight profiles |
| Test | `TheAlgorithms/Python` | 10 | Evaluate the frozen profile |
| Blind | `pallets/click`, `pytest-dev/pluggy` | 10 | Evaluate repositories excluded from weight selection |

Only validation results can select the profile. Test and blind results never
feed back into weight selection. The selected profile is then compared with the
frozen V1 localizer on Top-1, Top-3, Top-5, MRR, MAP, and localization latency.

## Measured Results

| Split | Cases | V1 Top-1/3/5 | V2 Top-1/3/5 | V1 MRR/MAP | V2 MRR/MAP | V2 latency ms | Gate |
| --- | ---: | --- | --- | --- | --- | ---: | --- |
| Validation | 15 | 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 115.6527 | pass |
| Test | 10 | 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 2.3250 | pass |
| Blind | 10 | 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 16.0943 | pass |

The test-plus-blind ablation contains 20 cases:

| Profile | Top-1 | Top-3 | Top-5 | MRR | MAP |
| --- | ---: | ---: | ---: | ---: | ---: |
| Rule only | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Fusion | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Graph only | 0.6500 | 0.9000 | 0.9500 | 0.7656 | 0.7572 |
| Dynamic only | 0.4500 | 0.8000 | 0.9500 | 0.6517 | 0.6350 |
| LLM only | 0.2000 | 0.6000 | 0.7000 | 0.4529 | 0.4517 |

## Interpretation And Limits

The current GitHub mutation template deliberately contains rule-detectable bug
patterns. Consequently, rule-only is perfect and this experiment does **not**
prove that fusion is better than rule-only. It proves the score decomposition,
dynamic-evidence boundary, repository-disjoint selection protocol, deterministic
ablations, and V1 non-regression gate.

No live LLM localization scorer was configured for this run. The LLM-only row is
the deterministic ranking produced when all LLM signal values are zero and must
not be described as model quality. A harder benchmark containing semantic bugs,
weak/static-rule-negative defects, real stack traces, and live-provider runs is
required before claiming incremental utility from every signal.

## Reproduction

```powershell
python -m code_intelligence_agent.evaluation.localization_split_evaluation `
  datasets/localization_v2/split_protocol.json `
  outputs/v2_phase4_localization `
  --require-non-regression
```

```powershell
python -m pytest -q `
  tests/test_evidence_v2_localization.py `
  tests/test_localization_split_evaluation.py `
  tests/test_repository_test_fault_localization.py `
  tests/test_coverage_runner.py
```

The machine-readable acceptance record is
`docs/v2/phase4_localization_metrics.json`.
