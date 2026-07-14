# Evidence Memory Ablation

- Status: `pass`
- Reason: `all_memory_ablation_expectations_met`
- Cases: 8
- Runs: 16

## With vs Without Memory

| Mode | Completion | Fact Recall | Constraint Preservation | Failed Patch Avoidance | Repeated Patch Rate | Stale Reuse | Avg Retrieved | Avg Prompt Chars | Avg Runtime (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| without_memory | 0.1250 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.0000 | 0.0000 | 2.00 | 0.0159 |
| with_memory | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 710.12 | 0.2277 |

## Acceptance Gates

- `memory_completes_all_controlled_tasks`: pass
- `memory_improves_task_completion`: pass
- `memory_preserves_all_controlled_constraints`: pass
- `memory_avoids_all_known_failed_patches`: pass
- `memory_does_not_reuse_stale_repo_evidence`: pass
- `disabled_ablation_retrieves_no_records`: pass

## Limitations

- The suite measures structured retrieval and policy-hint utility, not live-model reasoning quality.
- Cross-repo patterns are eligible only when their source validation authority is sandbox pytest.
