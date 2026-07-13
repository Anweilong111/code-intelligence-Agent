# Phase 5 Adaptive Patch Generation And Strict Validation

## Result

Phase 5 turns patch generation into an evidence-driven Agent action rather than
a fixed rule-first pipeline. Rule, LLM, and Hybrid modes share one provenance
contract, one safety policy, and one layered verification policy. Hybrid mode
can start with the LLM when the top-ranked functions have no supported
deterministic rule or when semantic evidence materially exceeds static-rule
evidence. It preserves a rule fallback when the model is unavailable.

The final success authority is executed targeted tests plus a passing full
repository regression. A target-only pass is stored as an unverified candidate,
not a verified repair. LLM Judge output remains advisory.

The focused Phase 5 regression contains 97 passing tests in 48.92 seconds. The
complete repository regression contains 1203 passing tests, zero failures, and
completed in 733.07 seconds on the recorded development environment.

## Adaptive Generation Policy

The planner examines the five highest-ranked functions and records:

- supported and unsupported static rule IDs;
- maximum static evidence;
- maximum semantic, LLM, or traceback pressure;
- model availability;
- total candidate budget and LLM budget cap.

The deterministic decision contract is:

```text
Rule mode   -> rule_only
LLM mode    -> llm_only

Hybrid mode:
  no model or zero LLM budget
      -> adaptive_rule_fallback
  no supported deterministic rule
      -> adaptive_llm_first
  semantic_pressure > static_pressure + 0.15
      -> adaptive_llm_first
  otherwise
      -> adaptive_rule_first
```

Unused budget from the first generator is transferred to the second generator.
The report preserves planned budget, effective budget, produced count, order,
reason, and carry-over behavior. This makes it possible to distinguish a policy
decision from the candidates that were actually produced.

## Candidate Provenance

Every candidate records its exact generator (`rule_based`, `llm`,
`rule_based_reflection`, or `llm_reflection`) and normalized generator family
(`rule` or `llm`). It also records the generation strategy, generator budget,
localization basis, static rule IDs, parent candidate, and execution/reflection
context.

LLM prompts are not copied into public reports. Prompt provenance contains:

- prompt kind (`patch_generation` or `patch_reflection`);
- contract version (`patch_prompt_v2`);
- stable fingerprint and character count;
- top-level JSON fields;
- `raw_prompt_persisted=false`.

This supports reproducibility and attribution without persisting potentially
sensitive repository context.

## Unified Patch Safety Gate

All initial and reflected candidates pass through the same policy before
sandbox execution:

| Check | Enforcement |
| --- | --- |
| AST and function scope | Parse the replacement as one function and reject out-of-scope edits |
| Signature and decorators | Preserve the public function contract unless an explicit rule permits a change |
| Authorized path | Resolve the target under the repository root and reject traversal or a different target file |
| Test integrity | Reject edits to test files by default |
| Sensitive files | Reject credentials, environment files, private keys, and related sensitive paths |
| Patch size | Enforce changed-line and change-ratio budgets |
| Dangerous APIs | Reject newly introduced `eval`, `exec`, dynamic import, shell execution, unsafe YAML, destructive removal, and related calls |
| Dependencies | Allow standard library, repository-local modules, declared dependencies, and explicit allowlists; reject unknown additions |
| Diff integrity | Rebuild the unified diff and require an exact normalized match |
| Failed-patch memory | Reject prior failed diff or fixed-source fingerprints |
| Semantic risk | Reject blocked behavioral anti-patterns before execution |

Repository-local import authorization checks the repository root, `src` layout,
and ancestors of the target file. Declared dependency authorization parses
`pyproject.toml`, `setup.cfg`, and root `requirements*.txt` files. It does not
execute packaging scripts.

## Semantic Validation

The semantic validator compares AST behavior summaries of the old and new
function. It blocks:

- removal of all input dependence;
- introduction of an input-independent constant return;
- new broad exception swallowing.

It warns, but does not automatically block, when control flow collapses, an
explicit error contract becomes weaker, or only some parameter dependencies are
removed. Warnings remain visible in the validation report and still require
behavioral evidence from tests.

## Layered Verification

Validation is performed in this order:

1. Python syntax and function AST validation.
2. Unified safety policy.
3. Semantic-diff validation.
4. Import validation in a copied temporary checkout.
5. Targeted failing-test validation.
6. Full repository regression validation.
7. Generated boundary probe when the rule and function signature are supported.
8. Optional non-authoritative LLM Judge risk review.

Import validation compares the baseline checkout with the patched checkout.
An unchanged pre-existing import failure is reported as
`baseline_failed_unchanged`; a new patch-induced import failure blocks a verified
repair.

Boundary probes currently cover `possible_index_overrun`,
`missing_len_zero_guard`, `dict_missing_key_guard`, and
`inverted_empty_guard`. They run the isolated function in a constrained
subprocess with a short timeout. Unsupported signatures are reported as
`not_run`; the system does not invent boundary evidence.

The strict success predicate is:

```text
verified_repair =
    targeted_tests_pass
    and syntax_pass
    and safety_gate_pass
    and semantic_validation_not_blocked
    and import_validation_not_regressed
    and boundary_probe_not_failed
    and full_repository_tests_pass
```

If only the targeted test passes, the artifact is
`repository_test_candidate.patch` with claim
`targeted_candidate_unverified`. Only a verified repair produces
`repository_test_repair.patch`.

## Reflection Contract

Reflection reads the parent diff, stdout, stderr, traceback, failed test IDs,
failure classification, prior judge feedback, caller/callee context, user
constraints, and failed patch fingerprints. A reflected candidate must be
meaningfully different from failed ancestors and must pass the same unified
safety gate. Reflection depth and width remain bounded; repeated failures end in
an auditable blocker.

## Controlled Evaluation

The controlled dataset contains one deterministic rule-supported defect, one
semantic direct-repair defect, and one semantic defect that requires reflection.
Each case runs independently in Rule, LLM, and Hybrid mode, for nine total runs.

| Mode | Candidate success | AST valid | Safety pass | Target pass | Regression safe | Verified repair | Reflection recovery | Attribution |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Rule | 0.3333 | 1.0000 | 1.0000 | 0.3333 | 0.3333 | 0.3333 | 0.0000 | 1.0000 |
| LLM | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.3333 | 1.0000 |
| Hybrid | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.3333 | 1.0000 |

The semantic Hybrid runs selected `adaptive_llm_first`; the deterministic case
selected `adaptive_rule_first`. The reflection case was credited to
`llm_reflection`, not to the rule generator or the initial LLM candidate.

These LLM responses are deterministic offline fixtures. The experiment proves
orchestration, safety, attribution, reflection, and sandbox contracts. It does
not measure live-provider quality and must not be presented as a real GitHub
repair rate. The machine-readable evidence is
`docs/v2/phase5_patch_metrics.json`.

## Reproduction

```powershell
python -m code_intelligence_agent.evaluation.patch_strategy_evaluation `
  datasets/patch_evaluation/v2_patch_strategy_controlled_cases.json `
  outputs/v2_phase5_patch_strategy `
  --format markdown `
  --require-pass
```

Run the focused Phase 5 regression:

```powershell
python -m pytest `
  tests/test_patch_safety_v2.py `
  tests/test_boundary_probe.py `
  tests/test_patch_generation_policy.py `
  tests/test_patch_strategy_evaluation.py `
  tests/test_repository_test_patch_candidates.py `
  tests/test_repository_test_patch_validation.py -q
```
