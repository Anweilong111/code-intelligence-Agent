# Phase 3 Evidence-Layered Agent Memory

## Result

Phase 3 upgrades the previous JSON state snapshot into a traceable memory system
that participates in planning and patch generation. The implementation remains
dependency-light: structured filtering and deterministic relevance scoring are
used before considering embeddings or a vector database.

The full regression contains 1177 passing tests, zero failures, and completed
in 608.16 seconds on the recorded development environment.

## Five Memory Layers

| Layer | Stored evidence | Primary consumer |
| --- | --- | --- |
| Working Memory | Current state, latest intent/action, verification, blocker and replan | AgentController |
| Session Memory | User goal, constraints, active scope, strategy preferences and compressed conversation | Intent Router, Planner, chat session |
| Repo Memory | Repository profile, Program Graph summary, Top-k localization, test command and result | Planner, fault localization explanation |
| Repair Memory | Patch attempts, fingerprints, sandbox outcomes, failure categories and reflection | Patch generator, Reflection/Replan |
| Cross-repo Pattern Memory | Generalized repair patterns promoted only from sandbox-verified success | Planner and patch strategy hints |

The legacy `long_term_pattern_memory` report key is retained as a compatibility
alias, but its content now points to verified cross-repo records. Failed patches,
heuristic bug labels and LLM opinions cannot enter the long-term success store.

## Evidence Record Contract

Each memory record contains a deterministic `memory_id` and fingerprint plus:

- `layer` and `kind`;
- `source`, `created_at`, and `updated_at`;
- `repo`, `repository_ref`, and `session_id`;
- `evidence_path` and `confidence`;
- `validation.status` and `validation.authority`;
- `version_scope`, `status`, `expires_at`, and `stale_reason`;
- a compact `summary`, structured `content`, and retrieval `keywords`.

This contract lets the report answer both "what fact was used?" and "which
artifact makes that fact auditable?" API keys and sensitive fields still pass
through the existing recursive redaction gate before persistence.

## Retrieval Pipeline

The retrieval algorithm is `structured_relevance_v1`.

First, it rejects records that are deleted, inactive, expired, from another
session, from another repository, or tied to a different commit. Global
cross-repo patterns bypass commit matching only after sandbox verification.

For each remaining record, the score is:

```text
score = 0.35 * confidence
      + 0.35 * query_token_overlap
      + layer_prior
      + task_type_boost
      + 0.03 * has_traceable_evidence
```

Layer priors are 0.18 Working, 0.17 Session, 0.16 Repair, 0.14 Repo and 0.10
Cross-repo. Task boosts favor constraints, repair history, test evidence or the
current state only when the current query requests that evidence. The score is
capped at 1.5 and ties are resolved deterministically by confidence and
`memory_id`.

Only the highest-ranked eight records are injected by default. The Planner
receives each selected ID, source, evidence path, confidence, retrieval score
and reason. Patch generation receives the same compact retrieval plus policy
hints. The complete failed-patch fingerprint set remains a safety filter even
when an individual failure record is outside Top-k.

## Versioning And Expiry

Session-scoped memories require the current `session_id`. Repo and repair
memories require both repository identity and `repository_ref`. A commit
mismatch is classified as `stale_repository_version` before scoring, so an old
Top-k conclusion or patch cannot silently influence a new checkout.

Records also expose `expires_at`; an elapsed timestamp is filtered as
`expired`. Rebuilt records preserve their original creation time and update
their current evidence. Removed source facts are retained only as bounded
`superseded` or `stale` audit records.

## Conversation Compression

The operational memory does not append unbounded turns. When retained history
exceeds 40 turns, old turns are summarized into:

- total compacted turns;
- intent and action counts;
- first and last compacted timestamps;
- latest compacted intent and action.

The most recent 24 turns remain available in detail. User constraints, active
scope and strategy preferences are stored independently and therefore survive
compaction and resume. Tests cover a 45-turn session and verify that total turn
count and the "do not modify public API" constraint are preserved.

## Cross-Repo Promotion

A repair pattern is promoted only when the patch attempt has all of the
following evidence:

1. candidate result marked passed;
2. validation status marked pass/verified;
3. sandbox status marked pass/verified;
4. validation authority equal to `sandbox_pytest`.

The promoted record stores a generalized failure type, target shape and
generator, not the complete source diff. Multiple repositories increase its
evidence count and append fixed source commit references. Failed or merely
LLM-approved candidates remain in repo-scoped Repair Memory.

## User Controls

`memory-show` retrieves traceable Top-k records, optionally restricted to one
layer. `memory-delete` tombstones a deterministic record ID so a regenerated
view cannot silently restore it. `memory-reset` supports `session`, `repair`
and `all` repository-scoped state; destructive commands require `--yes`.
Cross-repo verified patterns are not deleted by a single session reset.

## Controlled Ablation

The controlled suite contains eight cases and runs each with retrieval disabled
and enabled, for 16 runs total.

| Mode | Completion | Fact Recall | Constraint Preservation | Failed Patch Avoidance | Repeated Patch Rate | Stale Reuse | Avg Retrieved |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Without Memory | 0.1250 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |
| With Memory | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 |

The stale-version case succeeds in both modes because neither mode is allowed
to use the old commit. The other cases require a constraint, failed patch,
strategy, test command, blocker or verified cross-repo pattern. This explains
the 0.125 disabled completion value.

These values quantify controlled retrieval and policy-hint behavior. They do
not measure live-provider quality, semantic patch correctness, or real GitHub
repair success. Those require the later blind-repository benchmark.

## Reproduction

```powershell
python -m code_intelligence_agent.evaluation.memory_ablation_evaluation `
  datasets/memory_evaluation/v2_memory_ablation_cases.json `
  outputs/phase3_memory_ablation `
  --format markdown `
  --require-pass
```

```powershell
python -m pytest -q `
  tests/test_evidence_memory.py `
  tests/test_memory_ablation_evaluation.py `
  tests/test_agent_session_memory.py `
  tests/test_agent_controller.py `
  tests/test_repository_test_patch_candidates.py
```

```powershell
python -m pytest -q
```

The machine-readable acceptance record is
`docs/v2/phase3_memory_metrics.json`.
