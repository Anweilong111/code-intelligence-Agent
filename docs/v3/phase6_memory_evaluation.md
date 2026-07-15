# V3 Memory Generalization Evaluation

- Status: `pass`
- Reason: `all_memory_generalization_gates_passed`
- Cases: 7
- Runs: 14

## Retrieval Ablation

| Mode | Completion | Recall | Avg Selected | Stale Reuse | Conflict Execution | Advisory Execution | Avg Runtime (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `without_memory` | 0.4286 | 0.0000 | 0.0000 | 0 | 0 | 0 | 0.0113 |
| `structured_v2` | 1.0000 | 1.0000 | 0.7143 | 0 | 0 | 0 | 0.1353 |

## Strategy Confidence

- Status: `pass`
- Evidence: 5 attempts across 3 repositories
- Success / failure: 3 / 2
- Confidence: 0.2307 (`wilson_lower_bound_95pct`)
- Decision use: `advisory_only`

## Long Session Summary

- Status: `pass`
- Compacted / retained: 26 / 24
- Preserved constraints: preserve public API
- Preserved blockers: targeted_test_failed

## Embedding Decision

- Status: `not_retained`
- Implemented: false
- Reason: The controlled benchmark requires exact provenance, scope, conflict, and authority matching. No semantic-near retrieval subset currently demonstrates incremental benefit over structured_v2, so adding an embedding store would be unsupported complexity.
- Revisit gate: Add a blind paraphrase/cross-repository subset and retain embeddings only if completion improves without stale, conflict, or advisory execution violations.

## Acceptance Gates

- `structured_memory_completes_all_controlled_cases`: pass
- `structured_memory_improves_over_no_memory`: pass
- `stale_memory_never_reused`: pass
- `conflicting_memory_never_becomes_execution_hint`: pass
- `cross_repo_memory_is_advisory_only`: pass
- `strategy_confidence_uses_success_and_failure_evidence`: pass
- `long_session_summary_preserves_decision_facts`: pass
- `embedding_store_not_added_without_uplift_evidence`: pass

## Claim Boundary

This is a deterministic memory-policy benchmark. It measures scope, authority, conflict handling, and retrieval utility, not live-model reasoning quality.
