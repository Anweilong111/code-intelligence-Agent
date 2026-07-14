# Evidence V2 Localization Split Evaluation

- Status: `pass`
- Reason: `evidence_v2_meets_v1_non_regression_gate`
- Selection Scope: `validation_only`
- Selected Profile: `evidence_v2_default`
- Candidate Profiles: 4
- V1 Non-Regression: `true`
- LLM Signal Available: `false`

## V1 vs Evidence V2

| Split | Cases | V1 Top-1 | V2 Top-1 | V1 Top-3 | V2 Top-3 | V1 Top-5 | V2 Top-5 | V1 MRR | V2 MRR | V1 MAP | V2 MAP | V2 Latency ms | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| validation | 15 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 270.4395 | pass |
| test | 10 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 3.9316 | pass |
| blind | 10 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 27.3592 | pass |

## Ablation On Unseen Evaluation Splits

| Profile | Cases | Top-1 | Top-3 | Top-5 | MRR | MAP | Latency ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rule_only | 20 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 19.0052 |
| without_graph | 20 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 19.0052 |
| without_dynamic | 20 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 19.0052 |
| fusion | 20 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 19.0052 |
| graph_only | 20 | 0.6500 | 0.9000 | 0.9500 | 0.7656 | 0.7572 | 19.0052 |
| dynamic_only | 20 | 0.4500 | 0.8000 | 0.9500 | 0.6517 | 0.6350 | 19.0052 |
| llm_only | 20 | 0.2000 | 0.6000 | 0.7000 | 0.4529 | 0.4517 | 19.0052 |

## Evidence Contract

- Weight selection uses only the validation split.
- Test and blind splits are evaluated after the profile is frozen.
- TestFailureScore requires executed failing-test identifiers.
- StackTraceScore requires dynamically parsed stack frames.
- LLM-only results are not attributed to an LLM when no scorer is configured.
- Sandbox or pytest evidence remains authoritative for repair success.