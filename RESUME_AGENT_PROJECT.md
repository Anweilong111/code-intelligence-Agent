# 代码智能 Agent 项目简历写法

## 项目名称

代码智能 Agent：面向公开 Python GitHub 仓库的仓库理解、缺陷定位与受控自动修复系统

英文名称：

Code Intelligence Agent for Python GitHub Repository Analysis, Fault Localization and Controlled Repair

## 一句话版本

构建面向公开 Python GitHub 仓库的代码智能 Agent，融合 AST / Call Graph / Program Graph、静态规则、动态测试证据、SBFL-style scoring、LLM patch generation、sandbox validation 与 reflection repair，通过 AgentController 的 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 闭环实现从仓库理解到缺陷定位和补丁验证的端到端可审计分析。

## 推荐简历描述

**代码智能 Agent：面向 Python GitHub 仓库的缺陷定位、测试诊断与自动修复系统**

- 设计并实现面向公开 Python GitHub 仓库的代码智能 Agent，支持 `owner/repo`、GitHub URL 和本地路径输入，自动完成仓库发现、源码筛选、结构建模、测试环境诊断、函数级 Top-k 缺陷定位、补丁候选生成、sandbox 验证与最终审计报告输出。
- 构建基于 AST / Call Graph / Program Graph 的程序结构建模模块，融合 `StaticRuleScore`、`GraphScore`、`DynamicEvidenceScore`、`SBFLScore` 形成 `FinalScore`，输出可解释的 Top-k suspicious functions 与信号贡献。
- 实现 AgentController 决策闭环，将每轮 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 写入 JSON/Markdown artifact，并根据测试状态、依赖缺失、pytest 超时、LLM 失败、安全门禁和补丁失败自动选择下一步 action 或输出 blocker。
- 接入 LLM patch generation、reflection repair 和 LLM judge reranking；所有候选补丁必须经过 JSON parse、AST/scope/signature/safety gate、patch apply 和 pytest sandbox 验证，最终成功标准由 `sandbox_pytest_decides_success` 决定。
- 构建 V1 评估体系：30 个真实 GitHub repo onboarding case、50 个 repair/evaluation case、9/9 required metrics measured，覆盖 onboarding success、Top-k localization、Pass@1、Pass@k、reflection uplift、blocker accuracy、sandbox success、average runtime 和 LLM cost。

## English Resume Version

**Code Intelligence Agent for Python GitHub Repository Analysis and Controlled Repair**

- Built an auditable code intelligence Agent for public Python GitHub repositories, supporting `owner/repo`, GitHub URL, and local path inputs with automated repository discovery, source filtering, structure modeling, test environment diagnosis, fault localization, patch candidate generation, sandbox validation, and final report generation.
- Designed an AST / Call Graph / Program Graph based analysis pipeline that combines static rule signals, graph propagation, dynamic test evidence, and SBFL-style scores into explainable function-level Top-k suspicious rankings.
- Implemented an AgentController loop with `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`, recording action decisions, confidence, risk, blockers, inputs, outputs, verification evidence, and next plans in JSON/Markdown artifacts.
- Upgraded planning to a controlled LLM Planner/Replanner pattern in agent-auto mode: the LLM proposes structured actions with `memory_used`, while action registry, risk policy, and sandbox gates retain final execution authority and rule fallback.
- Integrated LLM patch generation, reflection repair, and LLM judge reranking while enforcing JSON parsing, AST/scope/signature safety gates, patch application, and pytest sandbox validation; sandbox execution is the final success criterion.
- Built a V1 evaluation suite with 30 real GitHub onboarding cases, 50 repair/evaluation cases, and 9/9 measured metric contracts covering onboarding success, Top-k localization, Pass@k, reflection uplift, blocker accuracy, sandbox success, runtime, and LLM cost.

## 偏算法方向写法

- 提出多信号函数级缺陷定位框架，将 AST 静态规则、调用图传播、动态测试证据、SBFL 覆盖信号与风险惩罚融合为 `FinalScore`，输出 Top-k ranking、证据来源和信号贡献。
- 将补丁生成建模为受约束搜索问题，只在 Top-k suspicious functions 附近生成最小 diff，并通过 AST/scope/signature/safety gate 过滤高风险候选，降低 LLM 越界修改风险。
- 设计 execution-feedback reflection loop，将失败类型、pytest stdout/stderr、traceback、旧 diff fingerprint、target function source 和 caller/callee context 反馈给下一轮 refined candidate，再执行 sandbox 验证。
- 构建 direct success、reflection success、LLM failed blocker、environment blocker、no-test-oracle blocker、safety-gate blocker 等多类别评估矩阵，用 artifacts 证明 Agent 决策链和修复边界。

## 面试 1 分钟讲法

我做的是一个面向公开 Python GitHub 仓库的代码智能 Agent，不是简单调用大模型改代码。系统先做 repo discovery 和 source filtering，再用 AST、Call Graph、Program Graph 建模代码结构，融合静态规则、图传播、动态测试证据和 SBFL-style score 得到函数级 Top-k 缺陷定位。AgentController 会基于当前 artifacts 和 blocker 执行 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`，决定是运行测试、诊断环境、生成补丁、验证补丁，还是输出 blocker。补丁候选必须经过 JSON/AST/scope/safety gate 和 pytest sandbox，LLM judge 只参与排序，不替代执行验证。V1 评估覆盖 30 个真实 GitHub onboarding case、50 个 repair/evaluation case，并完成 9/9 required metrics measured。

## 面试 3 分钟讲法

这个项目分三层。

第一层是仓库理解。输入可以是 `owner/repo`、GitHub URL 或本地路径。系统会发现 ref、源码候选、配置文件、测试目录、pytest/unittest/tox/nox 信号，并输出 repository profile、structure、test discovery、environment diagnosis 和 execution plan。

第二层是算法定位。系统用 AST 提取函数、类、调用、导入、规则信号和边界信息；用 Call Graph 和 Program Graph 表示结构关系；再融合静态规则、动态图测试证据和 SBFL-style score 得到 `FinalScore`，输出函数级 Top-k suspicious ranking。这样可以先把 LLM 搜索空间压缩到高风险函数附近，而不是让模型读完整仓库后直接猜。

第三层是修复闭环。系统在 Top-k 函数附近生成 rule、LLM 或 hybrid patch candidates，每个候选都要经过 JSON parse、AST parse、scope/signature check、safety gate 和 patch apply。只有进入 sandbox pytest 并通过目标测试，才算成功。如果补丁失败，reflection loop 会读取失败类型、旧 diff、pytest 输出和函数上下文，生成 refined candidate 并重新验证。AgentController 会把每一步 Observe、Plan、Act、Verify、Reflect、Replan 写入 artifact。

最终我用 V1 evaluation summary 做验收：30/30 onboarding repositories completed，50 个 repair/evaluation case，9/9 required metrics measured，覆盖 localization、repair、reflection、blocker、sandbox、runtime 和 LLM cost。边界也很明确：没有 failing test、test oracle、可复现环境或安全修复条件时，系统输出 blocker 和下一步建议，不夸大为自动修复成功。

## 不建议写进简历的说法

- 不写“支持任意 GitHub 仓库 100% 自动修复 bug”。
- 不写“完全由大模型实现自动修复”。
- 不写“LLM judge 可以替代测试验证”。
- 不把 passing tests 场景说成“发现并修复真实 bug”。
- 不暴露任何 API key，只写通过环境变量配置 LLM provider / model。

## 推荐边界说明

当前项目定位是“面向公开 Python GitHub 仓库的智能分析与受控修复 Agent”。它可以分析仓库结构、测试环境、静态/动态证据、Top-k 定位、patch validation 或 blocker；但不承诺任意语言、任意依赖、任意真实 bug 都能自动修复。缺少 failing test、测试 oracle、可复现环境或安全修复条件时，Agent 会输出 blocker 和下一步建议。
