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
