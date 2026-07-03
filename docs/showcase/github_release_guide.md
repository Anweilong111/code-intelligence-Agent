# GitHub 发布与面试展示指南

这份指南用于把当前项目整理成一个可以上传 GitHub、写进简历、用于面试讲解的最终展示态。它不是新的功能设计文档，而是发布前的阅读入口和验收清单。

## 项目定位

推荐表述：

> 面向公开 Python GitHub 仓库的代码智能分析 Agent。用户输入 `owner/repo` 或 GitHub URL 后，系统自动完成仓库发现、源码筛选、结构建模、静态缺陷信号挖掘、函数级 Top-k 缺陷定位、测试环境诊断、可选 pytest 执行、补丁生成与沙箱验证，并通过 `AgentController` 输出 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 的可审计智能分析报告。

不要表述为：

- 任意语言仓库都支持。
- 任意 GitHub 仓库都能 100% 自动修复真实 bug。
- 只靠大模型就能完成端到端修复。
- passing tests 场景等价于发现并修复真实 bug。

## 推荐阅读顺序

| 顺序 | 材料 | 作用 |
| ---: | --- | --- |
| 1 | `README.MD` 顶部 100 行 | 快速理解项目定位、运行命令、Agent 闭环和验收结果 |
| 2 | `docs/examples/README.md` | 查看 3 类真实 GitHub 仓库案例 |
| 3 | `docs/showcase/report_samples.md` | 查看面试可读的报告摘录 |
| 4 | `docs/showcase/robustness_matrix.md` | 查看真实仓库鲁棒性、blocker 和依赖/runner 画像 |
| 5 | `RESUME_AGENT_PROJECT.md` | 直接复制或改写简历 bullet |
| 6 | `INTERVIEW_QA_AGENT_PROJECT.md` | 准备面试问答 |
| 7 | `PROJECT_REPORT_BEGINNER_GUIDE.md` | 从 0 学习项目每一步怎么做、为什么做、结果是什么 |

## 三类必须展示的真实仓库案例

| 案例 | 仓库 | 展示重点 | 结论 |
| --- | --- | --- | --- |
| 可测试仓库 | `pytest-dev/pluggy` | pytest、src-layout、passing tests、regression guard | Agent 不虚构 bug，而是输出需要 failing test / bug report 的 blocker |
| blocker 仓库 | `octocat/Hello-World` | 非 Python / 无可分析源码 | Agent 输出 `source_import_or_parse_missing` 和下一步建议 |
| 修复与反思 | `TheAlgorithms/Python` | Top-k 定位、patch generation、sandbox validation、reflection loop | 初始补丁失败后，depth=1 refined patch 通过目标 pytest |

每个案例都应该能回答这些问题：

- 用户输入了哪个仓库？
- Agent Observe 到了什么？
- 当前 stage 是什么？
- blocker 是什么？
- selected action 是什么？
- Top-k suspicious function 是什么？
- testability / repairability 状态是什么？
- next action 是什么？
- 哪些 artifact 可以证明结论？

## 简历推荐写法

短版：

> 代码智能 Agent：基于 AST / Call Graph / Program Graph / SBFL / GraphScore 的 GitHub Python 仓库缺陷定位与自动修复系统，支持 AgentController 闭环规划、pytest sandbox 验证、patch reflection；已通过 5 仓库端到端 acceptance suite，并完成 9 仓库 P3 产品化鲁棒性矩阵验证。

算法向 bullet：

- 构建面向公开 Python GitHub 仓库的代码智能 Agent，支持 `owner/repo` 与 GitHub URL 输入，自动完成仓库发现、源码筛选、结构建模、测试诊断、函数级缺陷定位、补丁生成与验证报告输出。
- 设计基于 AST、Call Graph、Program Graph 的代码结构建模模块，融合 `StaticRuleScore`、`GraphScore`、`DynamicEvidenceScore`、`SBFLScore` 形成 `FinalScore`，实现函数级 Top-k suspicious ranking。
- 实现 `AgentController` 决策闭环，基于 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 自动选择源码扩展、测试执行、环境诊断、patch validation 或 blocker 输出。
- 构建 patch generation + sandbox validation + reflection loop，在 `TheAlgorithms/Python` gronsfeld case 中实现失败补丁反思修复，depth=1 refined candidate 通过目标 pytest 验证。
- 设计多仓库 acceptance suite 与 P3 产品化鲁棒性矩阵，覆盖可测试仓库、无 Python 仓库、无测试命令仓库、tox/nox 环境诊断、复杂 pyproject、大仓库 source slicing、patch/reflection 修复等场景；P3 矩阵达到 9/9 agent passed、9/9 objective compliance、9/9 AgentController loop complete。

## 面试讲解路线

1 分钟版本：

> 我做的是一个代码智能 Agent，不是简单调用大模型改代码。系统先解析 GitHub 仓库，构建 AST、Call Graph 和 Program Graph，再融合静态规则、图传播、动态测试证据和 SBFL 分数得到函数级 Top-k 定位。AgentController 会根据当前证据执行 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`，决定运行测试、诊断环境、生成补丁、验证补丁，或者输出 blocker。

3 分钟版本建议按三层讲：

| 层次 | 讲什么 |
| --- | --- |
| 架构 | 输入 GitHub 仓库，输出 JSON/Markdown artifacts，核心是 AgentController 闭环 |
| 算法 | AST / Call Graph / Program Graph + 多信号 FinalScore + Top-k 定位 |
| 验证 | pytest sandbox、patch validation、reflection loop、acceptance suite |

面试官追问“这是不是 workflow”时，回答重点是：

- workflow 是固定顺序；
- AgentController 会根据 artifact 和 blocker 改变下一步；
- passing tests、无 Python 源码、环境缺 runner、补丁失败会走不同 replan；
- 每一步都有 Observe / Plan / Act / Verify / Reflect / Replan 的审计记录。

## 发布前验收命令

必须运行：

```bash
python -m pytest tests/test_readme_showcase_consistency.py tests/test_github_repo_intelligence_suite.py -q
```

建议运行：

```bash
python -m pytest tests/test_github_repository_profile.py tests/test_repository_test_environment.py tests/test_agent_controller.py -q
```

如果要复现 5 仓库 acceptance：

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite datasets/github_cases/repo_intelligence_agent_cli_default_output_acceptance.example.json outputs_smoke/repo_intelligence_agent_cli_default_output_acceptance_current --require-success
```

## 完成标准

- README 顶部可以作为 GitHub 首页快速入口。
- `docs/examples/` 能展示 3 类真实仓库。
- `docs/showcase/` 能展示报告样例、鲁棒性矩阵和发布指南。
- `RESUME_AGENT_PROJECT.md` 可以直接改写进简历。
- `INTERVIEW_QA_AGENT_PROJECT.md` 可以直接用于面试复习。
- 文档明确说明边界，不夸大任意仓库 100% 自动修复。
- 关键测试通过。
## P3 产品化鲁棒性验收

详细索引：

[p3_product_robustness_matrix.md](p3_product_robustness_matrix.md)

复现 9 仓库公开 GitHub 矩阵：

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite datasets/github_cases/repo_intelligence_p3_product_robustness.example.json outputs_smoke/repo_intelligence_p3_product_robustness_current --reuse-existing-reports --require-success
```

当前验证结果：

| Metric | Result |
| --- | ---: |
| Runs | 9 |
| Agent passed runs | 9/9 |
| Objective compliance pass runs | 9/9 |
| AgentController loop complete runs | 9/9 |
| Repository structure modeled runs | 8 |
| Repo graph ready runs | 8 |
| Patch/reflection repair-ready runs | 1 |

这部分应该表述为鲁棒性矩阵，而不是“每个公开仓库都能自动修复”。P3 同时覆盖轻量 AgentController planning、环境诊断、无测试命令诊断、source-import blocker 和 repair/reflection 深链路。
