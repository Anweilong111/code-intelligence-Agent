# Code Intelligence Agent 示例报告索引

本目录用于 GitHub 展示和面试讲解，整理了 3 类真实 GitHub 仓库运行结果。原始输出来自：

`outputs_smoke/repo_intelligence_agent_cli_default_output_acceptance_current/`

## 为什么要整理成示例

完整输出目录包含大量 JSON/Markdown artifact，适合审计，但不适合面试官快速阅读。因此这里把每个仓库压缩成 5 个问题：

1. 用户输入了什么仓库？
2. Agent 观察到了什么？
3. Agent 如何规划下一步？
4. 静态/动态图证据给出了什么 Top-k 定位结果？
5. 最终是通过、阻塞、还是进入补丁验证与反思？

## 示例列表

| 示例 | 仓库 | 场景 | 关键结果 |
| --- | --- | --- | --- |
| [可测试仓库：pluggy](testable_repo.md) | `pytest-dev/pluggy` | Python 项目、pytest 可执行、自然测试通过 | 执行 `139 passed`，Top-1 为 `TagTracer.get`，Agent 将通过测试转成 regression guard，并要求提供 failing test / bug report 才能进入修复 |
| [Blocker 仓库：Hello-World](blocker_repo.md) | `octocat/Hello-World` | 非 Python/无可分析源码 | Agent 输出 `source_import_or_parse_missing`，选择 `adjust_source_filters`，并给出需要改变分析范围或提供外部证据的下一步 |
| [修复与反思：TheAlgorithms/Python](repair_reflection_repo.md) | `TheAlgorithms/Python` | gronsfeld case、patch validation、reflection | Top-1 定位 `gronsfeld`，初始补丁失败，reflection 生成 depth=1 refined patch，最终 1 个候选验证成功 |

## V1 可提交样例报告包

[v1_sample_reports.md](v1_sample_reports.md) 整理了 3 个可直接随仓库提交的 v1 样例报告摘要，覆盖 `pypa/sampleproject`、`pytest-dev/pluggy` 和 `octocat/Hello-World`。这些页面不依赖本地运行产物目录，适合 GitHub 展示。

[agent_demo_artifact_checklist.md](agent_demo_artifact_checklist.md) lists the
required main report, execution trace, decision report, and memory report for
the normal-analysis, blocker, and repair-reflection demos.

## V1 readiness audit

[v1_readiness_audit.md](v1_readiness_audit.md) summarizes the tracked 30
onboarding cases, 50 repair/evaluation cases, and 9 required metric contracts
used to support the v1 evaluation target.
It also documents resumable onboarding slices and
`v1_onboarding_slice_aggregate`, which turns partial 30-repository runs into
auditable progress evidence before final metric aggregation.

[v1_evaluation_summary.md](v1_evaluation_summary.md) records the current metric
snapshot: 30/30 onboarding repositories completed, 9/9 required metrics
directly measured, no proxy metrics, and LLM cost tracked through standalone
token usage plus configured pricing evidence.

## 顶层 Agent 入口 live smoke

[top_level_agent_live_smoke.md](top_level_agent_live_smoke.md) 记录了两类公开仓库 live run：`pytest-dev/iniconfig` 验证测试通过且无缺陷信号时的零退出审计报告，`pallets/itsdangerous` 验证依赖环境 blocker 时的 AgentController 处置路径。两者都通过 `python -m code_intelligence_agent agent <GitHub URL>` 输出 repo profile、graph、test diagnosis、Top-k/边界说明、AgentController trace 和 final audit report。

## LLM 修复 readiness / blocker

[llm_repair_readiness.md](llm_repair_readiness.md) 说明真实 LLM 修复 smoke 的环境变量要求，并记录 hybrid no-key run 如何把 `missing_llm_api_key` 处理成可审计 blocker，同时保留规则候选、安全门和 sandbox pytest 验证。

## 验收摘要

这批示例对应的 acceptance suite 已通过：

- Runs: 5
- Agent Passed Runs: 5/5
- Objective Compliance Pass Runs: 5/5
- Agent Controller Loop Complete Runs: 5/5
- Repository Test Patch Validation Successes: 1
- Repository Test Reflection Successes: 1
- pytest 覆盖的项目测试数：`total=161, passed=140, failed=21, errors=21`

其中失败/错误计数来自 repair case 的受控失败覆盖和预先存在的回归基线，并不表示 Agent 主流程失败；suite 的 expectation 和 acceptance gate 均为 pass。
## P3 9 仓库产品化鲁棒性矩阵

更广的公开仓库覆盖见：

`outputs_smoke/repo_intelligence_p3_product_robustness_current/`

展示索引见：

[../showcase/p3_product_robustness_matrix.md](../showcase/p3_product_robustness_matrix.md)

This matrix adds `psf/requests`, `pallets/click`, `Textualize/rich`,
`tiangolo/fastapi`, and `karpathy/nanoGPT` on top of the compact examples. It is
useful when you need to show that the Agent handles more than three curated
repositories while still reporting blockers instead of over-claiming repair.
