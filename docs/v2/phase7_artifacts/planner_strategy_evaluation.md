# Planner Strategy Evaluation

- Status: `pass`
- Reason: `all_controlled_planner_expectations_met`
- Cases: 14
- Runs: 42

## Strategy Metrics

| Planner | Completion | Valid Action | Invalid Proposals | Repeated Action | Avg Actions | Blocker Accuracy | Avg Runtime (ms) | Tokens | Cost (USD) | Safety Rejects | Fallbacks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rule | 1.0000 | 1.0000 | 0 | 0.0000 | 1.0000 | 1.0000 | 0.7791 | 0 | 0.00000000 | 0 | 0 |
| llm | 1.0000 | 1.0000 | 2 | 0.0000 | 1.0000 | 1.0000 | 1.6716 | 1560 | 0.01560000 | 8 | 11 |
| hybrid | 1.0000 | 1.0000 | 2 | 0.0000 | 1.0000 | 1.0000 | 1.6754 | 1560 | 0.01560000 | 9 | 12 |

## Limitations

- This Phase 2 suite evaluates planning and safety behavior, not repository repair success.
- LLM responses are deterministic offline fixtures; live-provider quality is evaluated separately.
