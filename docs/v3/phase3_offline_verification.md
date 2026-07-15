# V3 Phase 3 Offline Verification

- Overall status: `partial`
- Offline foundation: `pass`
- Live LLM/Hybrid evaluation: `blocked_provider_billing_or_quota`
- Verified: `2026-07-16`
- Accepted real bugs: `20`
- Rule trials: `20/20 complete`
- Rule RunRecords: `58`, schema errors `0`
- Focused tests: `134 passed`
- Full regression: `1408 passed, 2 skipped in 755.54s`
- Release hygiene: `5/5 pass`, `0` raw API-key findings

## Preparation Audit

| Gate | Result |
| --- | ---: |
| Cases prepared | 20/20 |
| Model-context audits | 20/20 pass |
| Analysis-scope ground-truth audits | 20/20 pass |
| Real source files in analysis scope | 25/25 |
| Real source files with an editable region | 25/25 |
| Ground-truth files requiring module regions | 7 |
| Selections using ground truth | 0 |
| Model contexts exposing ground truth | 0 |

The preparation artifact is
`outputs_v3/phase3_preparation_authoritative/evaluation.json`, SHA-256
`02c0390bd11dd7247574b9bc165d5431b3dd156c92d57ede799f8f8c7465bc53`.
It was generated before any Rule/LLM/Hybrid candidate was evaluated.

All model-visible localization rows map to authorized function regions. The
context audit found zero absolute local paths, relative runtime traversal paths,
secret-like values, unauthorized localization rows, and auxiliary-directory
module regions.

## Rule Baseline

| Metric | Result |
| --- | ---: |
| pass@1 | 0/20 (0.0000) |
| verified repairs | 0/20 (0.0000) |
| AST-valid candidates | 49/53 (0.924528) |
| Safety-passing candidates | 49/53 (0.924528) |
| Targeted-test passes | 0/49 (0.0000) |
| Full regressions reached | 0 |
| Provider/environment blockers | 0 |
| Test-file modification rejections | 0 |
| Model tokens/cost | 0 / USD 0.00 |

The Rule baseline is a negative control, not a repair success claim. Five cases
produced no applicable Rule candidate, four generated candidates with invalid
Python AST, and 49 safe candidates still failed the real targeted test. No
failure was removed from the denominator.

The authoritative evaluation is
`outputs_v3/phase3_rule_baseline_final/evaluation.json`, SHA-256
`3c80b3d6509cf478561ca6cf7441a68494ffe1020d66de29ccd99d01cbeff4b5`.
Its JSONL RunRecords have SHA-256
`20c66d77dbcb21b3ce650b09d00c4fdc73af73469c2ea62e1bc4c7a9c8c708ff`.
All 20 trials contain an input fingerprint covering the protocol, bug SHA,
evidence, localization, selected context, edit scope, and repair implementation.

## Integrity Finding

The first diagnostic Rule run revealed that candidate limits were applied
before test functions were excluded. Forbidden test edits could consume the
bounded Rule budget even though the safety gate later rejected them. That run
is superseded rather than reported as final evidence.

The corrected generator removes test functions and test paths from localization
rankings before applying the candidate limit, retains a second post-generation
guard, and has a regression test where a rank-1 test function cannot displace a
rank-2 production function. The final baseline contains zero test-edit
rejections; no safety or acceptance gate was weakened.

Independent live-model trials can run through a bounded worker pool. Each trial
uses a separate model client, failed-candidate history, sandbox workspace,
attempt directory, and trial ID; a concurrency regression test verifies that
three trials execute on three independent workers.

Each V3 HTTP request now runs in a trusted short-lived worker with a parent
wall-clock deadline covering process startup, proxy, DNS, TLS, response headers,
and response body. A 2026-07-16 real DeepSeek probe passed, followed by a 6/6
trial smoke case whose 26,202-file artifact scan found zero API-key hits. The
subsequent full batch returned HTTP 402, so paid execution was stopped. These
trials were then invalidated by the amended protocol and are not counted as
release metrics.

HTTP 402 is classified as `billing_or_quota`. Authentication, authorization,
billing/quota, and unavailable-model failures activate a circuit breaker: no
new trial or case is submitted after already-running workers finish. Once
provider access is restored, the amended protocol must be rerun; historical
Trial-level blockers require `--retry-blockers`.

The second protocol amendment moves this check before repair work. One frozen
request runs with reasoning disabled and at most 16 output tokens. It must
receive an HTTP 200 chat-completion envelope from the exact frozen model. Its
tokens, cost, and latency are recorded as `provider_preflight_overhead`; it is
not a repair Trial and cannot affect pass@1 or pass@3. The response content is
discarded, leaving only hashes and allowlisted metadata. Both the system Prompt
file and runtime request Prompt are pinned by SHA-256 and checked before a
provider call is allowed.

The final amended-protocol preflight returned HTTP 402 in 543 ms with zero tokens and
zero cost. It submitted zero of 120 Trials, created no case directory, kept the
20 cases in the missing denominator, wrote only three evaluation files, and
produced zero raw-key findings. After billing/quota is restored, a preflight-
only blocker can be retried with the same command; `--retry-blockers` remains
necessary only for historical Trial-level blocker attempts.

Whole-module candidates must also preserve existing function, method, class,
generic-parameter, and decorator contracts. Live-model RunRecords distinguish
the frozen prompt-template hash from the complete request-prompt hash and retain
the provider-returned model ID, but never the raw private reasoning payload.

## Remaining Phase 3 Work

The frozen matrix still requires 60 LLM and 60 Hybrid trials: three independent
trials per strategy for each of 20 bugs. Those runs must use a current
environment-injected key and must report real pass@1/pass@3, direct and
reflection success, exact generator attribution, tokens, cost, latency, and all
provider/environment/application failures.

Until that matrix completes, Phase 3 remains `partial` and no real-model repair
rate is claimed.
