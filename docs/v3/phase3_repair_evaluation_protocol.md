# V3 Phase 3: Real-Model Repair Evaluation Protocol

## Purpose

Phase 3 evaluates whether the Agent can turn evidence from a reproducible real
Python failure into a verified source patch. It compares Rule, LLM, and Hybrid
generation on the same 20 accepted fixed-SHA bugs and preserves every failed
candidate in the denominator.

This phase separates three claims:

1. `evaluation status=pass` means the requested trial matrix is complete and
   every RunRecord satisfies the frozen schema.
2. `targeted test pass` means a candidate fixes the observed failing test.
3. `verified repair` means the candidate also passes the fixed full-regression
   command after syntax and safety validation.

A complete evaluation may therefore pass with a zero repair rate. Static
analysis, model output, or an LLM judge never overrides sandbox test results.

## Frozen Inputs

- Protocol: `datasets/v3_real_bugs/experiment_protocol.json`.
- Accepted benchmark: `docs/v3/phase1_real_bug_catalog.json`.
- Runtime bindings: `docs/v3/phase1_environment_profiles.json`.
- Reproduction seeds: ignored bug-SHA checkouts under `outputs_v3/reproduction`.
- Model context: failure evidence and selected bug-SHA source only.
- Oracle: manifest-pinned targeted commands followed by the full-regression
  command.

Every seed is audited against the reproduction artifact SHA, case ID, bug
commit, overlay files, and overlay hashes. A fix checkout is never a trial seed.

## Trial Matrix

| Strategy | Independent trials per case | Candidate order |
| --- | ---: | --- |
| Rule | 1 | bounded deterministic rule candidates |
| LLM | 3 | one direct model candidate, then bounded reflection |
| Hybrid | 3 | Rule candidates, then LLM direct and bounded reflection |

The independence unit is `(case, strategy, trial_index)`. Candidate history is
not shared across trials. Provider retries remain inside the same model request
and do not create a new trial. Reflection can inspect only the current trial's
parent candidate and sanitized verification evidence.

## Agent Loop

### 1. Observe

The controller loads the accepted case and its real bug reproduction. Dynamic
evidence is rebuilt from the failing targeted execution, including normalized
test nodes, traceback frames, failure category, and bounded diagnostics.

### 2. Plan

For small repositories, all Python source files may be analyzed. For large
repositories, the scope selector starts from failing tests and traceback files,
then follows bounded local imports, reverse importers, and lexical path matches.
The large-repository limit is 40 source files. Ground-truth files and fix
contents are not inputs to scope selection.

Fault localization ranks functions from the selected bug-SHA scope. The edit
planner exposes at most 24 bounded function regions plus a small number of
eligible module regions. Test code, unsafe paths, secret-like text, oversized
regions, overlapping regions, and initialization/build modules are excluded.

After selection is frozen, an audit compares the chosen analysis/edit scope
with ground truth. This audit measures recall only; it cannot change selection
or model context.

### 3. Act

Rule generation uses only non-test production rankings. The candidate limit is
applied after test functions are removed, so rejected test edits cannot consume
the Rule budget.

LLM generation receives a JSON context and must return JSON only. Each file edit
must name a controller-authorized relative path and the SHA-256 of its original
region. The response parser rejects unknown paths, stale hashes, overlapping
module/function edits, malformed JSON, and empty replacements.

The model cannot issue shell commands or choose test commands. It proposes
source replacements; the rule safety controller owns all execution.

### 4. Verify

Each candidate is evaluated in a fresh workspace copied from the bug-SHA seed.
The verifier runs the following gates in order:

1. response schema and editable-region authorization;
2. replacement AST and reconstructed full-file AST validity;
3. no test edits, path escape, function/method/class contract change, dangerous API, undeclared
   dependency change, excessive diff, or repeated failed patch;
4. atomic application to an independent workspace;
5. manifest-pinned targeted tests with the case runtime;
6. manifest-pinned full regression when targeted tests pass.

No full regression is run after a targeted-test failure. Environment and
provider blockers are classified separately from code-repair failures.

### 5. Reflect And Replan

When an LLM candidate fails and the protocol permits another reflection round,
the controller builds a new bounded context containing:

- the parent candidate identifier and proposed replacements;
- safety, application, targeted-test, and regression summaries;
- sanitized failure evidence;
- failed diff/source fingerprints from the current trial.

The model must return a complete replacement candidate again. A repeated patch
is rejected before execution. The frozen protocol permits at most two reflection
rounds. A successful reflected candidate is recorded separately from direct
success.

## Leakage And Secret Controls

The model context audit fails a case before model execution if it contains a fix
commit, gold patch hash/content, test answer, secret-like token, absolute local
root, or unauthorized source. Repository text is marked untrusted.

Repository-external path fields retain only an `<external-path:basename>`
placeholder; absolute paths in failure text are replaced by `<external-path>`.
Raw provider payloads and private reasoning are not persisted. RunRecords keep
only response identifiers, content hashes, character counts, token usage,
latency, retry metadata, and provider model metadata. HTTP error bodies are
represented by byte count and SHA-256 only. API keys are read from the current
process environment and are never written to artifacts.

## RunRecord And Attribution

Every candidate produces one schema-validated RunRecord containing:

- case, repository, bug SHA, split, strategy, trial, and candidate identity;
- generator family and exact Rule/direct/reflection generator identifier;
- requested and provider-returned model IDs, template and complete request
  prompt hashes, controls, response hash, and retry count;
- input/cache/output/reasoning token counts, cost, and latency;
- AST, safety, targeted-test, regression, and semantic-validation states;
- outcome and one normalized failure layer/category;
- patch, validation, and test artifact references.

Rule candidates always have zero model tokens and cost. In Hybrid mode, success
is attributed to the actual winning candidate family. A Rule success does not
inherit LLM cost, and an LLM success is not credited to Rule.

## Metrics

For `N=20` accepted cases:

```text
pass@1 = cases with a verified repair in trial 1 / N
pass@3 = cases with a verified repair in any of trials 1..3 / N
verified repair rate = cases with a verified repair in any requested trial / N
reflection recovery = cases first repaired by reflection / N
AST-valid rate = AST-valid generated candidates / parsed candidate records
safety pass rate = safety-passing candidates / safety-evaluated candidates
targeted pass rate = targeted-passing candidates / targeted-executed candidates
regression pass rate = regression-passing candidates / regression-executed candidates
```

Provider and environment blockers remain visible in trial completeness and
failure reports. They are not relabeled as failed source repairs. Token, cost,
latency, and retry totals include every persisted provider call represented by a
RunRecord.

## Failure Taxonomy

Failures are assigned to the earliest authoritative layer:

- `generation`: no candidate, malformed model JSON, or unauthorized response;
- `provider`: authentication, rate limit, network, timeout, or provider error;
- `syntax`: invalid replacement or reconstructed module AST;
- `safety`: forbidden path/edit/API/dependency/signature or duplicate patch;
- `environment`: workspace, runtime, dependency, or test-process blocker;
- `targeted_test`: observed defect remains or the target command fails;
- `full_regression`: target passes but the complete regression fails;
- `semantic_validation`: the Phase 5 deterministic semantic gate rejects a
  patch or lacks the evidence required for a verified-repair claim.

Phase 5 now runs API/AST semantic checks, patched-workspace consistency,
minimality, target differential execution, and reverse mutation after full
regression. Case-specific boundary/property commands remain conditional; an
incomplete required oracle yields `unverified_suggestion`, not a verified
repair.

## Resume Semantics

Each completed trial writes an immutable attempt result and a `latest.json`
pointer. Resume reuses only a schema-compatible complete trial whose input
fingerprint matches the current protocol, bug SHA, dynamic evidence,
localization, selected model context, analysis/edit scope, and critical repair
implementation files. Provider/environment blockers are reused unless
`--retry-blockers` is explicit. New attempts receive new directories and trial
identifiers; prior attempts remain available for audit.

## Commands

Prepare and audit all 20 cases without calling a model:

```powershell
python -m code_intelligence_agent v3-repair-eval `
  outputs_v3/phase3_preparation `
  --prepare-only `
  --strategies rule,llm,hybrid `
  --format markdown `
  --require-pass
```

Run the deterministic Rule baseline:

```powershell
python -m code_intelligence_agent v3-repair-eval `
  outputs_v3/phase3_rule_baseline `
  --strategies rule `
  --rule-candidate-limit 5 `
  --targeted-timeout 180 `
  --regression-timeout 900 `
  --format markdown `
  --require-pass
```

Run live LLM and Hybrid trials only after injecting a current key into the same
process environment:

```powershell
$env:CIA_LLM_API_KEY = '<current-key>'
python -m code_intelligence_agent v3-repair-eval `
  outputs_v3/phase3_live_model `
  --strategies llm,hybrid `
  --live-model `
  --max-workers 3 `
  --targeted-timeout 180 `
  --regression-timeout 900 `
  --format markdown `
  --require-pass
```

The `--live-model` flag is an explicit paid-execution gate. Missing current
credentials fail before any case is run.

## Boundary

The committed Phase 3 framework and Rule baseline prove orchestration,
isolation, safety, attribution, and metric reconstruction. Real LLM/Hybrid
repair rates may be reported only after all 120 independent model trials (20
cases times 3 LLM plus 3 Hybrid trials) complete under the frozen protocol.
