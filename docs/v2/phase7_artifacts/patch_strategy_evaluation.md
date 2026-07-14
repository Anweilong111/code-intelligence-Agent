# Phase 5 Patch Strategy Evaluation

- Status: `pass`
- Reason: `all_controlled_patch_expectations_met`
- Cases: 3
- Runs: 9
- Success Authority: `sandbox_targeted_and_full_regression_tests`

## Strategy Metrics

| Mode | Candidate Success | AST Valid | Safety Pass | Target Pass | Regression Safe | Verified Repair | Reflection Recovery | Attribution | Avg Runtime (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rule | 0.3333 | 1.0000 | 1.0000 | 0.3333 | 0.3333 | 0.3333 | 0.0000 | 1.0000 | 1280.2533 |
| llm | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.3333 | 1.0000 | 3327.4613 |
| hybrid | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.3333 | 1.0000 | 3574.8704 |

## Runs

| Case | Mode | Strategy | Candidates | Target | Regression | Verified | Reflection | Winner | Expected |
| --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |
| deterministic_index_overrun | rule | rule_only | 2 | true | pass | true | false | rule_based | true |
| deterministic_index_overrun | llm | llm_only | 1 | true | pass | true | false | llm | true |
| deterministic_index_overrun | hybrid | adaptive_rule_first | 3 | true | pass | true | false | rule_based | true |
| semantic_none_normalization | rule | rule_only | 0 | false | skipped | false | false | none | true |
| semantic_none_normalization | llm | llm_only | 1 | true | pass | true | false | llm | true |
| semantic_none_normalization | hybrid | adaptive_llm_first | 1 | true | pass | true | false | llm | true |
| semantic_parse_port_reflection | rule | rule_only | 0 | false | skipped | false | false | none | true |
| semantic_parse_port_reflection | llm | llm_only | 1 | true | pass | true | true | llm_reflection | true |
| semantic_parse_port_reflection | hybrid | adaptive_llm_first | 1 | true | pass | true | true | llm_reflection | true |

## Limitations

- The LLM responses in this Phase 5 suite are deterministic offline fixtures.
- The suite validates orchestration, attribution, safety, reflection, and sandbox contracts; it does not measure live-model repair quality.
- The three controlled cases are contract tests, not a real-world GitHub repair-rate estimate.
