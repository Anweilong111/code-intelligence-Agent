# V3 Phase 7 Final Verification

## Release Decision

The frozen V3 release is `pass` with `claim_eligible=true`. Offline Phase 0-6
evidence and the live repair artifact both pass their independent gates.

| Gate | Result |
| --- | --- |
| Real fixed-SHA benchmark | 20 accepted cases from 6 repositories |
| Repository test-process startup | 19/20 |
| Live trial completeness | 120/120, 0 missing |
| RunRecord audit | 423/423 pass |
| Provider/model | DeepSeek / `deepseek-v4-pro` |
| Prompt and model metadata drift | none |

## Live Repair Results

| Strategy | pass@1 | pass@3 | Reflection case recovery | Verified trials | Cost USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| LLM | 8/20 (0.40) | 10/20 (0.50) | 7/20 (0.35) | 25/60 | 1.839613 |
| Hybrid | 6/20 (0.30) | 9/20 (0.45) | 3/20 (0.15) | 22/60 | 1.006873 |

LLM verified trials contain 16 direct successes and 9 Reflection recoveries.
Hybrid verified trials contain 19 direct successes and 3 Reflection recoveries.
All 22 Hybrid winners are attributed to the LLM generator family; Rule produced
no verified winner. One provider timeout remains a separate Hybrid blocker and
is retained in the audited denominator.

Provider-access preflight passed against the exact frozen model. Its USD
0.000035 cost and 1072 ms latency are reported separately and are not counted as
a repair trial.

## Statistical Boundary

The LLM pass@1/pass@3 Wilson 95% intervals are `[0.2188, 0.6134]` and
`[0.2993, 0.7007]`. Hybrid intervals are `[0.1455, 0.5190]` and
`[0.2582, 0.6579]`. The intervals overlap, so the 20-case result does not support
a claim that either live strategy is universally superior.

## Evidence

- [Unified Markdown report](phase7_unified_evaluation.md)
- [Unified machine-readable report](phase7_unified_evaluation.json)
- [Frozen release protocol](phase7_release_protocol.md)
- [Architecture and Agent design](v3_architecture_and_agent_design_cn.md)
- [10-minute demonstration guide](v3_ten_minute_demo_guide_cn.md)
- [Chinese resume and interview pack](../career/v3_resume_interview_pack_cn.md)

The committed aggregate contains metrics, hashes, metadata, and selected
audited examples. API keys, raw prompts, raw provider payloads, private
reasoning, benchmark checkouts, and local workspaces are not committed.

## Final Test Note

The V3-focused suite passed 137 tests with one explicit skip in 8.47 seconds.
The complete repository regression passed 1412 tests with two explicit Windows
symlink-fixture skips in 676.95 seconds. It used a workspace-local pytest
`--basetemp` because the host Windows temporary directory has unstable sandbox
ACL initialization; no model API key was present in the test process.

The machine-readable verification file also records release-hygiene and
clean-archive evidence. Values are added only after the corresponding commands
finish; a documentation pass never substitutes for those executable gates.
