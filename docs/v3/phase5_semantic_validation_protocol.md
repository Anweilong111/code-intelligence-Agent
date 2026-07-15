# V3 Phase 5 Patch Semantic Validation Protocol

## Objective

Phase 5 prevents a patch from being called a verified repair merely because one
targeted test and the existing regression suite pass. It adds an auditable
post-regression semantic gate that checks interface compatibility, semantic
risk, patch/workspace consistency, minimality, old-versus-new behavior, and the
necessity of every candidate edit.

The gate never reads the gold patch, fix commit, issue solution, or LLM judge
opinion during an Agent repair trial. A model may propose a candidate, but only
deterministic validators and pinned test executions determine whether the
candidate is claim-eligible.

## Position In The Repair Chain

Every executable candidate follows this order:

1. Parse the candidate JSON and bind every edit to an authorized Top-k region.
2. Run AST, path, scope, signature, changed-line, dangerous-API, duplicate, and
   sensitive-file safety checks.
3. Copy the bug seed into an isolated trial workspace and apply the candidate.
4. Run the manifest-pinned targeted test in the case-pinned Python runtime.
5. Run the complete manifest-pinned regression command.
6. Run the Phase 5 semantic validator.
7. Return `verified_repair` only if every required semantic check passes.

A failure before step 6 remains attributed to its original layer. A semantic
failure is attributed to `semantic_validation`. Missing semantic evidence or
an environment blocker produces `unverified_suggestion`, not a verified repair.

## Semantic Checks

The semantic artifact stores each check independently. A check has one of four
states: `pass`, `fail`, `blocker`, or `not_applicable`.

| Check | Required | Purpose |
| --- | --- | --- |
| `api_contract_compatibility` | yes | Compare AST-derived function, method, class, decorator, argument, default, and annotation contracts in every changed file. |
| `static_semantic_diff` | yes | Reject input-dependency removal, new hard-coded constant returns, broad exception swallowing, and removed module definitions. |
| `patched_workspace_consistency` | yes | Confirm each authorized replacement is present in the patched workspace and reject statically resolvable imports of removed cross-file symbols. |
| `patch_minimality` | yes | Count changed lines per edit and enforce an independent changed-line budget. |
| `generated_boundary_property_probe` | when supported | Generate deterministic empty/singleton/dictionary boundary inputs for supported rule families. |
| `target_behavior_differential` | yes | Demonstrate that the bug seed fails the target while the patched workspace passes the same target. |
| `reverse_mutation_sensitivity` | yes | Revert every candidate edit independently and require the target or full suite to detect each reversion. |
| `manifest_semantic_commands` | when configured | Run benchmark-authored boundary, property, mutation, or differential tests through pinned pytest/unittest commands. |

The generated boundary probe and manifest semantic commands are conditional.
They are not reported as passed when no applicable probe exists. The overall
repair can still be claim-eligible when these optional checks are
`not_applicable`, but the artifact preserves that absence.

## API And Type Contract Compatibility

For each changed Python file, the validator parses the bug-side and patched
module and snapshots:

- functions and async functions, including positional-only, positional,
  keyword-only, variadic arguments, defaults, annotations, return annotations,
  type comments, and available type parameters;
- classes, including bases, class keywords, decorators, and available type
  parameters;
- methods and nested classes under their qualified class names.

Removing a recorded contract is always rejected. Changing a recorded contract
is rejected unless an existing trusted rule explicitly authorizes a signature
change. LLM response JSON cannot grant itself that authorization. New contracts
are recorded but are not rejected solely for being new.

This is intentionally stricter than a public-name-only check: private
function/method contracts in a changed file are also compared. The stricter
scope lowers compatibility risk but can reject legitimate refactors, so any
such rejection remains visible in the failure taxonomy.

## Static Semantic Diff

Function edits are compared as AST behavior summaries. The validator blocks:

- removal of all dependencies on previously read parameters;
- replacement of input-dependent behavior by a constant-only return;
- newly introduced broad exception swallowing such as `except Exception: pass`;
- semantic ASTs that cannot be parsed.

It also records, without automatically rejecting, risk warnings such as
collapsed control flow, weakened raise/assert behavior, and partial parameter
dependency removal.

Whole-module edits are checked for removed top-level functions/classes and
removed top-level assignments. Definition removal is blocking; assignment
removal is a warning here and is checked again by cross-file consistency.

## Patched Workspace And Cross-File Consistency

The candidate is re-bound to its authorized `(path, original_sha256)` region.
The validator then reads the actual patched workspace rather than trusting the
candidate payload:

1. Every changed path must remain inside the workspace, exist as a regular
   file, and not be a symbolic link.
2. A whole-module replacement must equal the patched module after newline
   normalization. A function replacement must occur in the patched source.
3. Both bug-side and patched changed modules must parse as Python ASTs.
4. Top-level symbols are computed for changed modules.
5. If a symbol was removed, statically resolvable `from module import symbol`
   references in the patched repository are inspected.
6. A still-imported removed symbol fails the check. Dynamic `__getattr__`
   exports are reported as warnings and left to runtime evidence.

The import scan is conservative. It does not claim full Python import or data
flow resolution and cannot prove dynamically constructed imports correct.

## Patch Minimality

Changed lines are computed with a deterministic sequence diff. For edit `i`:

```text
ChangedLines_i = sum(max(old_span, new_span)) for every non-equal diff opcode
```

For `E` edits across `F` files, the budget and score are:

```text
Budget = 80 * max(1, E)
CrossFilePenalty = 0.05 * max(0, F - 1)
MinimalityScore = max(
    0,
    1 - min(1, TotalChangedLines / Budget) - CrossFilePenalty
)
```

The score is diagnostic; the hard gate requires at least one real changed line
and `TotalChangedLines <= Budget`. Necessity is evaluated separately through
reverse mutation, so a small but redundant edit can still fail Phase 5.

## Target Differential Execution

The same manifest-pinned target is evaluated on both states:

```text
required relation = bug seed fails AND patched workspace passes
```

During a normal Agent trial, the patched result is the immediately preceding
targeted-test execution and is reused without changing its evidence. The bug
seed is executed again in its immutable reproduction workspace. During isolated
human-fix calibration, both sides are executed by the semantic evaluator.

If the bug seed now passes, the reproduction no longer demonstrates the target
defect. If the patch fails, the candidate is not a repair. Environment failures
on either side are blockers rather than code-failure evidence.

## Reverse Mutation And Test-Overfitting Signal

For each candidate edit, the validator creates a fresh copy of the patched
workspace and restores only that edit to its bug-side source. It then applies
this decision tree:

```text
target fails after reversion
    -> mutation killed by targeted test
target passes, full regression fails
    -> mutation killed by full regression
target passes, full regression passes
    -> mutation survives; candidate contains an unproven/redundant edit
environment or safe-reversion failure
    -> blocker
```

Every edit must be killed. A survivor prevents `verified_repair`. This catches
many test-overfit and unrelated-edit patterns because the test oracle must show
why each individual change is necessary.

This is patch-reversion mutation, not general mutation testing over every AST
operator. The reported mutation kill rate must therefore be described as
`reverse mutation kill rate`, not as a repository-wide mutation score.

## Boundary And Property Evidence

The deterministic generated probe currently supports four rule families:

- possible index overrun;
- missing zero-length guard;
- missing dictionary-key guard;
- inverted empty-input guard.

It executes bounded empty/singleton/small-container cases and rejects forbidden
exceptions such as `IndexError`, `ZeroDivisionError`, or `KeyError`. The probe
bootstrap uses the same case-pinned Python interpreter as the benchmark tests.
Unsupported functions, async functions, decorated functions, whole modules,
and LLM-only semantic repairs remain `not_applicable` unless the benchmark
supplies a semantic command.

Once a generated probe actually executes, it becomes a required semantic gate:
a forbidden exception fails the candidate, a timeout fails the candidate, and
failure to start the pinned interpreter is an environment blocker. It cannot be
silently ignored after producing evidence.

Benchmark-authored semantic commands must use the pinned `{python} -m pytest`
or `{python} -m unittest` form and one of the explicit kinds `boundary`,
`property`, `mutation`, or `differential`. Arbitrary modules and shell commands
are blocked.

## Claim-State Rules

The final semantic state is deterministic:

```text
any required fail           -> fail
else any required blocker   -> blocker
else any required N/A       -> not_applicable
else                        -> pass
```

Only `pass` sets `claim_eligible=true`. The repair executor maps states as:

| Semantic state | Trial outcome |
| --- | --- |
| `pass` | `verified_repair` |
| `fail` | `failed` at `semantic_validation` |
| `blocker` | `unverified_suggestion` |
| `not_applicable` | `unverified_suggestion` |

The experiment-protocol validator rejects a record that claims semantic pass
without the full details object and `claim_eligible=true`.

## Human-Fix Calibration Isolation

Human fixes are used only to estimate validator false rejection after the Agent
path has been defined. The calibration tool enforces:

1. An accepted Phase 1 case and matching fixed bug/fix commit checkouts.
2. Reproduction evidence that the bug target failed, the fix target passed, and
   the fix full regression passed.
3. The exact case-pinned Python/dependency runtime.
4. Production `.py` files only; test and non-Python files cannot enter the
   calibration candidate.
5. No signature-change waiver for the human-fix candidate.
6. Explicit artifact fields `human_fix_oracle_used=true`,
   `gold_patch_visible_to_model=false`, and `agent_repair_claim=false`.

Fix-side content is never passed to localization, planning, candidate
generation, reflection, or an LLM. Calibration results are not included in
pass@1, pass@3, verified-repair rate, or strategy attribution.

## Calibration Result

The initial calibration covers two real PySnooper defects:

| Case | Source edits | Semantic result | Reverse mutations killed |
| --- | ---: | --- | ---: |
| `bugsinpy-pysnooper-1` | 2 | pass | 2/2 |
| `bugsinpy-pysnooper-3` | 1 | pass | 1/1 |

Both human fixes passed all six required checks, with zero false rejections and
zero blockers. No supported generated boundary probe or manifest semantic
command applied to these two cases, and both absences are stored as
`not_applicable`.

This is a small same-repository development calibration. It demonstrates that
the current gates accept these known-correct fixes; it is not a false-rejection
confidence interval and does not establish Agent repair performance.

## Reproduction

```powershell
python -m code_intelligence_agent v3-semantic-eval `
  outputs_v3/phase5_semantic_calibration `
  --case-id bugsinpy-pysnooper-1 `
  --case-id bugsinpy-pysnooper-3 `
  --release-docs-dir docs/v3 `
  --require-pass
```

Committed evidence:

- `docs/v3/phase5_semantic_calibration.json`
- `docs/v3/phase5_semantic_calibration.md`
- `docs/v3/phase5_verification.json`
- `docs/v3/phase5_verification.md`

## Current Limits

- Automatic boundary/property generation is rule-specific, not universal.
- Reverse mutation measures edit necessity, not all possible semantic mutants.
- Static cross-file import checks do not resolve dynamic imports or arbitrary
  runtime export mechanisms.
- API checks are deliberately strict and can reject intentional refactors.
- The initial calibration has only two cases from one repository.
- The 120 paid live LLM/Hybrid trials remain pending until a fresh API key is
  injected through the environment. No Phase 5 result is a live-model claim.
