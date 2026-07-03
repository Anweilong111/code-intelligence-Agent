# 代码智能 Agent 项目简历写法

## 推荐项目名称

代码智能 Agent：面向 GitHub Python 仓库的缺陷定位、测试诊断与自动修复系统

英文写法：

Code Intelligence Agent for GitHub Python Repository Analysis, Fault Localization and Automated Repair

## 一句话项目介绍

构建一个面向公开 Python GitHub 仓库的代码智能 Agent，用户输入 `owner/repo` 或 GitHub URL 后，系统自动完成仓库拉取、源码筛选、结构建模、静态缺陷信号挖掘、函数级 Top-k 缺陷定位、测试环境诊断、可选 pytest 执行、补丁生成与沙箱验证，并通过 AgentController 执行 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 决策闭环，最终输出可审计分析报告。

## 最终推荐版

项目名称：

代码智能 Agent：面向 GitHub Python 仓库的缺陷定位、测试诊断与自动修复系统

简历一句话：

构建面向公开 Python GitHub 仓库的代码智能 Agent，结合 AST / Call Graph / Program Graph、静态规则、动态图测试证据与 SBFL 分数实现函数级 Top-k 缺陷定位，并通过 AgentController 的 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 闭环调度 DeepSeek LLM 补丁生成、pytest sandbox 验证、reflection 修复和 blocker 报告。

推荐 bullet：

- 设计并实现面向公开 Python GitHub 仓库的代码智能 Agent，支持 `owner/repo` 与 GitHub URL 输入，自动完成仓库发现、源码筛选、结构建模、测试环境诊断、函数级缺陷定位、补丁生成与可审计报告输出。
- 构建基于 AST、Call Graph、Program Graph 的程序结构建模模块，融合 `StaticRuleScore`、`GraphScore`、`DynamicEvidenceScore`、`SBFLScore` 为 `FinalScore`，输出可解释的 Top-k suspicious functions。
- 实现 `AgentController` 决策闭环，将每轮 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 写入 JSON/Markdown artifact，并根据 passing tests、无 Python 源码、环境缺 runner、补丁失败等 blocker 自动选择下一步动作。
- 构建 DeepSeek LLM patch generation + pytest sandbox validation + reflection loop，在 `TheAlgorithms/Python` gronsfeld case 中实现初始 LLM patch 直接修复、失败后 LLM refined patch 修复，以及缺失 LLM key 时的 blocker 审计。
- 设计多仓库 acceptance suite，覆盖 `pytest-dev/pluggy`、`octocat/Hello-World`、`TheAlgorithms/Python`、`pypa/sampleproject`、`karpathy/nanoGPT` 等真实仓库场景，验证可测试仓库、blocker 仓库、环境诊断和 patch/reflection 修复链路。

面试时建议补一句边界：

当前项目定位是“面向公开 Python GitHub 仓库的智能分析 Agent”，不承诺任意语言、任意依赖、任意真实 bug 都能 100% 自动修复；缺少 failing test、测试 oracle 或可复现环境时，Agent 会输出 blocker 和下一步建议。

## 简历短版

代码智能 Agent：基于 AST / Call Graph / Program Graph / SBFL / GraphScore 的 GitHub Python 仓库缺陷定位与自动修复系统，支持 AgentController 闭环规划、DeepSeek LLM patch/reflection、pytest sandbox 验证与 blocker 审计；已完成 `llm_direct_success`、`llm_reflection_success`、`llm_blocker` 三类 LLM 修复验收矩阵。

## 简历 Bullet 版本

- 构建面向公开 Python GitHub 仓库的代码智能 Agent，支持 `owner/repo` 与 GitHub URL 输入，自动完成仓库发现、源码筛选、结构建模、测试诊断、函数级缺陷定位、补丁生成与验证报告输出。
- 设计基于 AST、Call Graph、Program Graph 的代码结构建模模块，融合 `StaticRuleScore`、`GraphScore`、`DynamicEvidenceScore`、`SBFLScore` 形成 `FinalScore`，实现函数级 Top-k suspicious ranking。
- 实现 `AgentController` 决策闭环，基于 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 自动选择源码扩展、测试执行、环境诊断、patch validation 或 blocker 输出。
- 构建 DeepSeek LLM patch generation + sandbox validation + reflection loop，在 TheAlgorithms/Python gronsfeld case 中实现初始 LLM patch 直接通过、失败补丁经 reflection refined candidate 通过，以及 LLM key 缺失时 blocker 输出。
- 设计多仓库 acceptance suite 与 P3 产品化鲁棒性矩阵，覆盖可测试仓库、无 Python 仓库、无测试命令仓库、tox/nox 环境诊断、复杂 pyproject、大仓库 source slicing、patch/reflection 修复等场景；P3 矩阵达到 9/9 agent passed、9/9 objective compliance、9/9 AgentController loop complete。

## 更偏算法的写法

- 提出多信号函数级缺陷定位框架，将 AST 静态规则、调用图传播、动态图测试证据、SBFL 覆盖信号与风险惩罚融合为 `FinalScore`，并输出可解释 Top-k ranking 与信号贡献。
- 将补丁搜索建模为受约束候选生成与执行验证问题，在 Top-k suspicious functions 内生成最小 diff，通过 AST/scope/safety gate 过滤后进入 pytest sandbox，降低 LLM 或规则补丁的误修改风险。
- 设计 LLM reflection-based repair loop，根据补丁失败类型、pytest 输出、旧 diff fingerprint、动态 oracle 与函数上下文生成 refined candidate，实现从失败 LLM 补丁到成功补丁的闭环验证，并通过 direct / reflection / blocker showcase matrix 防止把 workflow 误写成 Agent。

## 1 分钟面试讲法

我做的是一个代码智能 Agent，不是简单调用大模型改代码。用户输入 GitHub 仓库后，系统先做 repo discovery 和 source filtering，然后用 AST、Call Graph、Program Graph 建模代码结构，再融合静态规则、图传播、动态测试证据和 SBFL 分数得到函数级 Top-k 缺陷定位。AgentController 会根据当前证据执行 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`，决定是运行测试、诊断环境、生成补丁、验证补丁，还是输出 blocker。项目有 5 仓库端到端 acceptance suite，并进一步扩展到 9 仓库 P3 鲁棒性矩阵，覆盖可测试仓库、非 Python blocker、无测试命令、复杂 pyproject、tox/nox 环境诊断、patch validation 和 reflection 修复场景。

## 3 分钟面试讲法

这个项目的核心是把程序分析、图算法、测试执行和大模型/规则补丁生成组合成一个可审计 Agent。第一层是仓库理解，系统会拉取或读取 GitHub 仓库，识别 Python 源码、测试框架、配置文件和可执行测试命令。第二层是结构建模，使用 AST 提取函数、类、调用、变量和规则信号，再构建 Call Graph 和 Program Graph。第三层是缺陷定位，系统将 `StaticRuleScore`、`GraphScore`、`DynamicEvidenceScore`、`SBFLScore` 融合成 `FinalScore`，输出 Top-k suspicious functions。第四层是修复闭环，在 Top-k 范围内生成候选 patch，经过 AST/scope/safety gate 后放入 sandbox 执行 pytest。如果失败，reflection loop 会读取失败类型和旧 patch，再生成 refined candidate。最后 AgentController 会把每轮观察、计划、动作、验证、反思和重规划写入 artifact，保证面试官可以审计它为什么继续、为什么停止、为什么阻塞。

## 5 分钟面试讲法

如果展开讲，我会分三部分：架构、算法、验收。

架构上，输入是 `owner/repo` 或 GitHub URL，输出是一组 Markdown/JSON 报告，包括 `github_repo_intelligence`、`github_repo_agent_controller`、`repository_structure`、`repo_graph`、`fault_localization`、`repository_test_execution_result`、`repository_test_patch_validation` 和 `reflection_trace`。这些 artifact 共同证明 Agent 的每一步不是黑盒。

算法上，项目不是直接让 LLM 猜 bug，而是先把仓库变成结构化图。AST 负责精确函数边界和规则检测，Call Graph 负责跨函数传播，Program Graph 统一静态结构、测试证据和数据/控制流信号。定位阶段融合多种分数形成 `FinalScore`，避免单一规则或单次模型判断带来的不稳定。修复阶段采用受约束 patch search，只在 Top-k 函数附近产生最小 diff，并用 sandbox 执行结果作为最终证据。

验收上，我先用了 5 个真实 GitHub 仓库做端到端 acceptance suite：`pytest-dev/pluggy` 展示可测试项目和 passing-test blocker，`octocat/Hello-World` 展示非 Python blocker，`TheAlgorithms/Python` 展示 patch validation 和 reflection 成功，另外还有 pypa/sampleproject 与 nanoGPT 覆盖环境/测试命令类问题。随后扩展到 9 仓库 P3 产品化鲁棒性矩阵，加入 `psf/requests`、`pallets/click`、`Textualize/rich`、`tiangolo/fastapi` 等复杂公开仓库。P3 结果是 9/9 agent passed、9/9 objective compliance、9/9 AgentController loop complete、8/9 repo graph ready，并保留 1 个 patch/reflection 成功样例。这个表达比较准确：它已经是一个可写进简历的算法向代码智能 Agent，但不夸大成任意仓库都能 100% 自动修复真实 bug。

## 不建议写进简历的说法

- 不写“支持任意 GitHub 仓库 100% 自动修复 bug”。
- 不写“完全由大模型实现自动修复”。
- 不把 passing tests 场景说成“发现并修复真实 bug”。
- 不暴露任何 API key，只写通过环境变量配置 LLM provider / model。

## P3 简历表达更新

如果要体现最新 P3 鲁棒性工作，可以使用这一版：

Code Intelligence Agent: built an auditable AgentController-driven Python repository analysis agent for public GitHub repositories. The system combines AST / Call Graph / Program Graph modeling, static rule signals, dynamic test evidence, SBFL-style scoring, constrained patch generation, sandbox validation, and reflection. It has a 5-repository end-to-end acceptance suite and a 9-repository P3 product-robustness matrix covering source filtering, cached GitHub discovery, src-layout projects, complex pyproject repositories, no-Python blockers, no-test-command blockers, environment diagnosis, and one verified patch/reflection repair path.

推荐新增 bullet：

- 将真实仓库评估从 5 仓库 acceptance suite 扩展到 9 仓库 P3 鲁棒性矩阵，覆盖 `pypa/sampleproject`、`pytest-dev/pluggy`、`psf/requests`、`pallets/click`、`Textualize/rich`、`tiangolo/fastapi`、`TheAlgorithms/Python`、`octocat/Hello-World` 和 `karpathy/nanoGPT`；达到 9/9 agent passed、9/9 objective compliance、9/9 AgentController loop complete、8/9 repo graph ready，并保留 1 个 repair/reflection 成功样例，同时明确不夸大任意仓库自动修复。
