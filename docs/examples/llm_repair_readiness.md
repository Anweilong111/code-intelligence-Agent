# LLM Repair Readiness and Blocker Behavior

This page documents the current LLM repair contract for the repository Agent.
It covers two related paths:

1. Real LLM repair smoke, which requires API keys in environment variables.
2. Hybrid no-key smoke, which intentionally clears those variables and verifies
   that the Agent reports an LLM blocker without fabricating repair success.

## Real LLM Repair Smoke

The real LLM repair suite is defined by:

`datasets/github_cases/repo_intelligence_llm_repair_smoke.example.json`

It requires keys outside the repository:

| Role | Accepted environment variables |
| --- | --- |
| Patch generation | `CIA_LLM_API_KEY` or `DEEPSEEK_API_KEY` |
| Patch judge | `CIA_JUDGE_API_KEY` or `DEEPSEEK_API_KEY` |
| Replan advisor | `CIA_REPLAN_LLM_API_KEY`, `CIA_LLM_API_KEY`, or `DEEPSEEK_API_KEY` |

The manifest requires:

- `repository_patch_generation_mode=llm`
- `repository_test_reflection_mode=llm`
- `patch_judge_mode=llm`
- real provider configuration, not placeholder keys
- AST/scope/minimal-diff safety checks before validation
- sandbox pytest as the final success authority

No API key is stored in code, README, tests, manifests, or generated showcase
docs. Reports only record provider, model, key presence, short fingerprint, and
request telemetry.

The optional LLM replan advisor is advisory-only. It writes a
`llm_replan_advisor` audit section with recommended action, confidence, risk,
blocker, and next plan, but the deterministic AgentController policy remains
the authority for the selected action.

## Hybrid No-Key Smoke

The no-key smoke is defined by:

`datasets/github_cases/repo_intelligence_hybrid_no_key_smoke.example.json`

It clears LLM key environment variables for the run. The expected behavior is
not success through LLM, but an auditable blocker:

| Field | Value |
| --- | --- |
| Repository | `TheAlgorithms/Python` |
| Ref | `6c0462028f547fc905a4d9a8cc956daed8a00cd8` |
| Target file | `ciphers/gronsfeld_cipher.py` |
| Patch generation mode | `hybrid` |
| LLM provider | `deepseek` |
| LLM model | `deepseek-v4-pro` |
| LLM patch status | `blocked` |
| LLM patch reason | `missing_llm_api_key` |
| LLM API key present | `false` |
| LLM request count | 0 |
| LLM token count | 0 |
| LLM estimated cost | 0 |

Rule fallback remains active in hybrid mode:

| Metric | Value |
| --- | ---: |
| Rule candidates | 4 |
| LLM candidates | 0 |
| Safety-blocked candidates | 0 |
| Sandbox-executed candidates | 4 |
| Successful sandbox candidates | 1 |

## Patch Validation Evidence

| Field | Value |
| --- | --- |
| Patch candidates status | `pass` |
| Patch validation status | `pass` |
| Repair ready | `true` |
| Validation scope | `narrow_and_unchanged_regression_baseline` |
| Final authority | `sandbox_pytest_decides_success` |
| Best rule | `missing_len_zero_guard` |
| Best variant | `return_default_on_empty` |

The successful narrow patch adds an empty-key guard to `gronsfeld`:

```diff
@@
     ascii_len = len(ascii_uppercase)
     key_len = len(key)
+    if not key_len:
+        return 0
     encrypted_text = ""
```

The broad regression command had a pre-existing unchanged baseline failure, so
the Agent reports a repair with a baseline-regression caveat instead of
claiming full-suite green status.

## Agent Decision

After patch validation, the controller selected:

| Field | Value |
| --- | --- |
| Selected action | `run_search_and_ablation_evaluation` |
| Current stage | `phase3_patch_validation` |
| Next stage | `phase4_search_and_evaluation` |
| Blocker | none |
| Reason | Patch validation is ready; evaluate search strategy and ablations. |

## Why This Matters

This smoke test proves three important Agent properties:

- LLM configuration is environment-only.
- Missing LLM credentials become a structured blocker, not a crash or fake
  success.
- Even when an LLM branch is blocked, hybrid mode can continue through rule
  candidates, safety gates, sandbox pytest validation, and final audit reporting.

When valid LLM keys are present, the real LLM repair smoke exercises the same
pipeline with LLM-generated candidates and LLM reflection enabled.
