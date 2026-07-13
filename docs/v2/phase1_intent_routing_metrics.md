# Phase 1 LLM Natural-Language Task Routing

## Result

Phase 1 passes its acceptance gates. Terminal conversation now routes a user
turn through forced `route_agent_intent` Function Calling when credentials are
available, validates the returned schema and arguments, and falls back to the
rule parser when configuration, transport, tool name, JSON, or schema checks
fail.

| Metric | Result | Gate | Status |
| --- | ---: | ---: | --- |
| Bilingual routing utterances | 112 | >= 100 | pass |
| Supported intents covered | 14/14 | 14/14 | pass |
| Rule fallback accuracy | 112/112 (100%) | >= 90% | pass |
| New `intent_router` line coverage | 97% | >= 85% | pass |
| Chat command actions mapped through Action Registry | 4/4 | 100% | pass |
| Continuous terminal conversation | 10 turns | >= 10 | pass |
| Full regression suite | 1150 passed, 0 failed | no V1 regression | pass |

The frozen V1 baseline contains 1127 passing tests. The Phase 1 suite contains
1150 passing tests and completed in 588.92 seconds on the recorded development
environment.

## Safety Boundary

- The model selects only one schema-defined intent; it cannot return a shell
  command or an arbitrary action ID.
- Repository-relative paths, Python function identifiers, candidate IDs,
  confidence, required context, and optional argument types are validated.
- Low-confidence or incomplete requests become `ask_for_clarification`.
- `--execute` remains explicit. Command-capable turns are mapped to a canonical
  Action Registry entry and reject shell control characters or mismatched
  runner prefixes before process creation.
- `rollback_last_action` never guesses a reverse diff. It requires an audited
  rollback artifact and explicit confirmation.
- Provider metadata is redacted; raw API keys are neither persisted nor
  included in reports.

## Reproduction

```powershell
python -m pytest tests/test_agent_intent_router.py tests/test_intent_routing_dataset.py tests/test_agent_session_memory.py -q
```

```powershell
python -m trace --count --missing --summary `
  --coverdir outputs/phase1_trace_coverage `
  --ignore-dir "$env:CONDA_PREFIX\Lib" `
  --module pytest `
  tests/test_agent_intent_router.py `
  tests/test_intent_routing_dataset.py `
  tests/test_agent_session_memory.py -q
```

```powershell
python -m code_intelligence_agent.evaluation.release_hygiene_audit `
  outputs/phase1_release_hygiene --require-pass
```

The machine-readable result is in
`docs/v2/phase1_intent_routing_metrics.json`.
