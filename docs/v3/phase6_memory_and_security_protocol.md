# V3 Phase 6 Memory Generalization and Repository Security Protocol

## Objective

Phase 6 makes two Agent properties enforceable rather than descriptive:

1. Retrieved memory has explicit decision authority. A relevant record is not
   automatically allowed to affect execution.
2. Repository-controlled text and code are untrusted. They cannot become Agent
   instructions, inherit host credentials, or execute outside the registered
   test and installation policies.

This phase does not add a vector database, a free-form shell tool, or a claim of
container-grade isolation.

## Structured Memory V2

Every normalized record now contains:

- `trust_class`: where the fact came from.
- `decision_use`: `execution_hint`, `advisory_only`, or `audit_only`.
- `conflict_status`, `conflict_group`, and `conflicts_with`.
- existing provenance: layer, source, repo, commit, session, evidence path,
  confidence, validation status, and validation authority.

### Decision-authority matrix

| Record | Scope/validation requirement | Decision use |
| --- | --- | --- |
| Explicit user constraint or strategy | Current session, user authority | `execution_hint` |
| Failed patch fingerprint | Current repo and commit, patch-validation source | `execution_hint` |
| Test command/result | Current repo and commit, sandbox pytest authority | `execution_hint` |
| Current blocker/controller state | Current repo/session | `execution_hint` |
| Repo profile, graph, localization summary | Current commit | `advisory_only` |
| Verified cross-repo repair pattern | Sandbox-observed source evidence | `advisory_only` |
| Stale, expired, superseded, conflicting, or invalid record | Any | filtered or `audit_only` |

`memory_policy_hints` consumes only non-conflicting `execution_hint` records.
Cross-repo patterns remain visible in `advisory_repair_patterns`, but never enter
the executable strategy, command, constraint, or failed-patch hint buckets.

### Scope invalidation

Filtering happens before relevance scoring:

1. Non-active and expired records are removed.
2. Session records must match the current session ID.
3. Repo-commit records must match both repository and commit ref.
4. Cross-repo patterns require sandbox-pytest validation.

A commit mismatch is reported as `stale_repository_version`; it is not silently
re-ranked below current evidence.

### Conflict arbitration

The retriever builds conflict groups before Top-k truncation. Explicit
`conflict_key` and `value` fields are preferred; deterministic normalization
also recognizes common public-API and repair-strategy directives.

When one group contains multiple values:

1. Every member becomes `audit_only`.
2. `requires_clarification=true` is emitted.
3. No conflicting value enters policy hints.
4. An LLM planner override is rejected with
   `conflicting_memory_requires_clarification`.
5. The rule-selected controller action remains authoritative.

This avoids resolving contradictory user intent by timestamp, retrieval score,
or model preference.

### Cross-repository strategy confidence

Successful and failed sandbox-observed attempts are deduplicated by repository,
commit, session, candidate, diff fingerprint, and outcome. Confidence is the
95% Wilson lower bound:

```text
p = successes / n
lower = (p + z^2/(2n) - z*sqrt(p(1-p)/n + z^2/(4n^2))) / (1 + z^2/n)
z = 1.96
```

The Phase 6 fixture contains 3 successes and 2 failures from 3 repositories.
The resulting confidence is `0.2307`, not `0.6000` and not `1.0000`. This is a
conservative support score and remains advisory-only.

### Long-session compaction

When retained turns exceed 40, the latest 24 remain verbatim and older turns are
compacted. The summary now preserves bounded, deterministic sets of:

- active constraints;
- repair strategy preferences;
- failed patch fingerprints;
- blockers;
- verification outcome counts;
- intent and action counts;
- a SHA-256 summary fingerprint.

The summary does not retain arbitrary old messages as executable instructions.

## Memory Ablation

The controlled dataset has seven cases covering current constraints, current
failed patches, stale commits, conflicting constraints, verified cross-repo
patterns, unverified cross-repo claims, and other-session evidence.

| Mode | Completion | Record recall | Stale reuse | Conflict execution | Advisory execution |
| --- | ---: | ---: | ---: | ---: | ---: |
| No memory | 0.4286 | 0.0000 | 0 | 0 | 0 |
| Structured V2 | 1.0000 | 1.0000 | 0 | 0 | 0 |

This benchmark measures deterministic memory-policy behavior, not LLM reasoning
or repository repair success.

### Embedding decision

An embedding store is intentionally not implemented. The current controlled
cases require exact scope, provenance, validation, and conflict matching, and no
blind semantic-near subset demonstrates incremental value. The decision is
recorded as `not_retained`, not as an embedding ablation win.

The feature may be reconsidered only after a repository-disjoint paraphrase
benchmark shows higher completion without increasing stale reuse, conflicting
execution, or advisory-execution violations.

## Untrusted Repository Content

Repository files, test output, tracebacks, static findings, and repository-derived
memory have `instruction_authority=none`.

Before they enter an LLM planner prompt:

1. A deterministic scanner detects instruction override, role impersonation,
   secret exfiltration, shell/tool directives, and safety-bypass language.
2. Flagged text is replaced by a SHA-256 quarantine marker. The raw flagged
   content is not included in the prompt.
3. Inputs are depth-, item-, and string-length bounded.
4. The prompt contains an explicit untrusted-data boundary.
5. If any injection signal exists, an LLM action override is rejected with
   `untrusted_repository_prompt_injection_detected`; the rule action remains.

The scanner is defense in depth. It does not claim to classify every possible
natural-language attack.

## Process and Filesystem Isolation

### Environment

Repository test, coverage, boundary-probe, overlay, and installation subprocesses
receive a newly constructed environment:

- only a small operating-system/runtime allowlist is inherited;
- secret-like names such as API keys, tokens, passwords, credentials, and
  provider key variables are removed;
- only validated per-action overrides are added;
- `HOME` and `USERPROFILE` point to a controlled sandbox directory;
- no secret value is written to an artifact.

Dependency installation may allow network because package download is its stated
purpose, but high-risk repository build hooks still require explicit
authorization and receive no host credentials.

### Network

Python repository processes load a `sitecustomize` guard that rejects external
socket connections while allowing loopback for asyncio and local test servers.
Native child executables can bypass a Python monkeypatch; full native-process
network denial therefore remains a container-level requirement.

### Resource limits

- Parent wall-clock timeout is enforced for every restricted process.
- POSIX uses a new process group plus CPU, address-space, and file-size rlimits.
- Windows uses a new process group and `taskkill /T /F` on timeout.
- Windows hard CPU, memory, and disk quotas require a container or Job Object;
  the artifact reports this limitation explicitly.

### Paths and symlinks

- Working directories containing `..` or absolute paths are rejected before
  process start.
- Repository trees are scanned without following links.
- Repository roots or entries that are symbolic links are rejected before test
  execution or sandbox copying.
- The current Windows host cannot create the symlink fixture; this is reported
  as a platform blocker while deterministic unit coverage remains retained.

## Hostile-Repository Evaluation

Eight controlled cases cover:

1. repository prompt injection;
2. malicious legacy `setup.py`;
3. repository-local `backend-path` build hook;
4. working-directory traversal;
5. repository symlink;
6. sensitive environment read;
7. external Python socket exfiltration;
8. infinite CPU loop.

All 8/8 are rejected, isolated, or accurately reported. This is a controlled
security regression suite, not proof that arbitrary hostile code is safe to run
without OS/container isolation.

## Reproduction

```powershell
python -m code_intelligence_agent v3-memory-eval `
  datasets/memory_evaluation/v3_memory_generalization_cases.json `
  outputs_v3/phase6_memory `
  --format markdown `
  --require-pass

python -m code_intelligence_agent v3-security-eval `
  outputs_v3/phase6_security `
  --format markdown `
  --require-pass
```

Committed reports:

- `docs/v3/phase6_memory_evaluation.json`
- `docs/v3/phase6_memory_evaluation.md`
- `docs/v3/phase6_security_evaluation.json`
- `docs/v3/phase6_security_evaluation.md`
- `docs/v3/phase6_verification.json`
- `docs/v3/phase6_verification.md`

## Acceptance Boundary

Phase 6 passes when stale or conflicting memory cannot become an execution hint,
cross-repo memory remains advisory, hostile fixtures are controlled or accurately
reported, and all focused/full regression and release-hygiene gates pass.

The pending 120 live Rule/LLM/Hybrid trials are independent Phase 3 evidence and
are not replaced by these deterministic memory and security results.
