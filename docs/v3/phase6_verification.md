# V3 Phase 6 Verification

## Result

Phase 6 passes its memory-authority, hostile-repository, regression, artifact,
and release-hygiene gates. Retrieved evidence is no longer allowed to influence
execution solely because it is relevant: scope, provenance, validation,
decision authority, and conflict state are enforced before policy hints reach
the controller.

Repository-controlled text and subprocesses are also treated as untrusted.
Prompt-injection content is quarantined, host secrets are removed from child
environments, external Python sockets are denied, unsafe paths and build hooks
are rejected, and runaway process trees are terminated.

## Memory Evidence

| Gate | Result |
| --- | ---: |
| Controlled cases / runs | 7 / 14 |
| No-memory completion | 0.4286 |
| Structured V2 completion | 1.0000 |
| Structured V2 record recall | 1.0000 |
| Stale memory reused | 0 |
| Conflicting memory executed | 0 |
| Advisory memory executed | 0 |
| Strategy observations | 3 success / 2 failure, 3 repositories |
| Strategy confidence | 0.2307, 95% Wilson lower bound |

Cross-repository repair patterns remain `advisory_only`. They can explain or
rank a possible strategy, but they cannot select an action, command, constraint,
or patch. Conflicting current-session directives become `audit_only` and force
clarification rather than timestamp- or model-based arbitration.

The embedding store remains unimplemented because the current benchmark shows
no semantic-near retrieval gain beyond exact structured retrieval. This is a
recorded non-adoption decision, not an embedding ablation victory.

## Security Evidence

All `8/8` controlled hostile-repository cases were rejected, isolated, or
accurately reported:

| Threat | Disposition |
| --- | --- |
| Repository prompt injection | rejected; raw instruction replaced by hash marker |
| Legacy `setup.py` hook | rejected before process start |
| Repository-local build backend | rejected from automatic execution |
| Working-directory traversal | rejected before process start |
| Repository symlink | platform blocker reported; rejection path unit tested |
| Host secret read | isolated; canary absent |
| External Python socket | isolated; policy block observed |
| Infinite CPU loop | isolated; process tree terminated on timeout |

## Test Evidence

| Gate | Result | Duration |
| --- | --- | ---: |
| Phase 6 core regression | 118 passed, 1 skipped | 15.63 s |
| Runtime/dynamic-entry regression | 116 passed | 108.98 s |
| All V3 tests | 119 passed, 2 skipped | 8.46 s |
| Full pytest suite | 1365 passed, 2 skipped | 752.07 s |
| Release hygiene | 5/5 checks, 509 candidate files | n/a |

The two skips are explicit Windows-host limitations: this environment cannot
create symbolic links in `test_runtime_security.py` or
`test_v3_semantic_validation.py`. Traversal and symlink rejection remain covered
without converting unavailable host behavior into a false pass.

The Phase 5 trial implementation hash was refreshed because Phase 6 changed
sandbox and boundary-probe implementation files. The semantic acceptance
contract and calibration claims did not change.

## Artifact Audit

- Release hygiene found no raw API keys, tracked runtime outputs, binary
  documents, tool-signature traces, or documentation that gives an LLM judge
  authority over pytest/sandbox.
- Committed Phase 6 data and reports contain no local absolute paths.
- The dataset, protocol, and generated JSON/Markdown reports are LF-normalized.
- The machine-readable verification file pins all release report, dataset,
  controller, memory, sanitizer, runtime guard, and evaluator hashes.

## Claim Boundaries

- Memory metrics are deterministic policy metrics, not live-LLM repair rates.
- Eight hostile fixtures are regression evidence, not a proof of arbitrary-code
  containment.
- The Python socket guard does not stop native child executables from opening a
  network connection.
- Windows hard CPU, memory, disk, and network quotas still require a container
  or Windows Job Object.
- The pending 120 live Rule/LLM/Hybrid trials remain independent and require a
  fresh environment-injected API key.

See the [Phase 6 protocol](phase6_memory_and_security_protocol.md),
[memory evaluation](phase6_memory_evaluation.md), and
[security evaluation](phase6_security_evaluation.md) for the full algorithms,
case evidence, and reproduction commands.
