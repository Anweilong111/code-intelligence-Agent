# 代码智能 Agent 项目简历写法

## 项目名称

代码智能 Agent：面向 GitHub Python 仓库的缺陷定位、测试诊断与自动修复系统

英文名称：

Code Intelligence Agent for GitHub Python Repository Analysis, Fault Localization and Automated Repair

## 一句话版本

构建面向公开 Python GitHub 仓库的代码智能 Agent，结合 AST / Call Graph / Program Graph、静态规则、动态测试证据和 SBFL-style scoring 实现函数级 Top-k 缺陷定位，并通过 AgentController 的 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 闭环调度 LLM patch generation、sandbox validation、reflection repair 和 blocker reporting。

## 推荐简历描述

**代码智能 Agent：面向 GitHub Python 仓库的缺陷定位、测试诊断与自动修复系统**

- 设计并实现面向公开 Python GitHub 仓库的代码智能 Agent，支持 `owner/repo`、GitHub URL 和本地仓库输入，自动完成仓库发现、源码筛选、结构建模、测试环境诊断、函数级缺陷定位、补丁候选生成、sandbox 验证与可审计报告输出。
- 构建基于 AST / Call Graph / Program Graph 的程序结构建模模块，融合 `StaticRuleScore`、`GraphScore`、`DynamicEvidenceScore`、`SBFLScore` 形成 `FinalScore`，输出可解释 Top-k suspicious functions。
- 实现 AgentController 决策闭环，将每轮 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 写入 JSON/Markdown artifact，并根据测试状态、环境依赖、缺失 oracle、安全门禁和补丁失败自动选择下一步 action。
- 接入 LLM patch generation、LLM reflection 和 LLM judge reranking；所有候选均经过 JSON parse、AST/scope/signature/safety gate、patch apply 和 pytest sandbox 验证，最终成功标准由 `sandbox_pytest_decides_success` 决定。
- 构建 P6 评估矩阵：覆盖 10 个真实 GitHub onboarding case、30 个 repair/evaluation case、5 个 LLM direct success、4 个 LLM reflection success、21 个 blocker case，P6 readiness audit 达到 24/24 checks pass。

## 英文简历版本

**Code Intelligence Agent for GitHub Python Repository Analysis and Repair**

- Built an auditable code intelligence Agent for public Python GitHub repositories, supporting `owner/repo`, GitHub URL, and local path inputs with automated repository discovery, source filtering, structure modeling, test environment diagnosis, fault localization, patch candidate generation, sandbox validation, and report generation.
- Designed an AST / Call Graph / Program Graph based analysis pipeline that combines static rule signals, graph propagation, dynamic test evidence, and SBFL-style scores into function-level Top-k suspicious rankings.
- Implemented an AgentController loop with `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`, recording action registry, policy trace, blockers, next actions, and verification evidence in JSON/Markdown artifacts.
- Integrated LLM patch generation, LLM reflection, and LLM judge reranking while enforcing JSON parsing, AST/scope/signature/safety gates, patch application, and pytest sandbox validation; final patch success is decided by sandbox execution rather than model judgment.
- Built a P6 evaluation matrix with 10 real GitHub onboarding cases, 30 repair/evaluation cases, 5 LLM direct-success cases, 4 LLM reflection-success cases, 21 blocker cases, and 24/24 P6 readiness checks passing.

## 更偏算法的写法

- 提出多信号函数级缺陷定位框架，将 AST 静态规则、调用图传播、动态图测试证据、SBFL 覆盖信号与风险惩罚融合为 `FinalScore`，输出可解释 Top-k ranking 和信号贡献。
- 将补丁生成建模为受约束搜索问题，只在 Top-k suspicious functions 附近生成最小 diff，并通过 AST/scope/signature/safety gate 过滤高风险候选，降低 LLM 或规则补丁的越界修改风险。
- 设计 execution-feedback reflection loop，将失败类型、pytest stdout/stderr、traceback、旧 diff fingerprint、target function source 和 caller/callee context 反馈给下一轮 LLM refined candidate，并再次执行 sandbox 验证。
- 构建 direct success、reflection success、LLM failed blocker、environment blocker、no-test-oracle blocker、safety-gate blocker 多类别评估矩阵，用可审计 artifacts 证明 Agent 决策链和修复边界。

## 面试 1 分钟讲法

我做的是一个面向公开 Python GitHub 仓库的代码智能 Agent，不是简单调用大模型改代码。系统先做 repo discovery 和 source filtering，再用 AST、Call Graph、Program Graph 建模代码结构，融合静态规则、图传播、动态测试证据和 SBFL-style score 得到函数级 Top-k 缺陷定位。AgentController 会基于当前 artifacts 和 blocker 执行 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`，决定是运行测试、诊断环境、生成补丁、验证补丁，还是输出 blocker。补丁候选必须经过 JSON/AST/scope/safety gate 和 pytest sandbox，LLM judge 只参与排序，不替代执行验证。P6 验证覆盖 10 个真实仓库 onboarding case 和 30 个 repair/evaluation case，其中包括 5 个 LLM direct success、4 个 LLM reflection success 和 21 个 blocker case。

## 面试 3 分钟讲法

这个项目分三层。

第一层是仓库理解。输入可以是 `owner/repo`、GitHub URL 或本地路径。系统会发现 ref、源码候选、配置文件、测试目录、pytest/unittest/tox/nox 信号，并输出 repository profile、structure、test discovery、environment diagnosis 和 execution plan。

第二层是算法定位。系统用 AST 提取函数、类、调用、导入、规则信号和边界信息；用 Call Graph 和 Program Graph 表示结构关系；再融合静态规则、动态图测试证据和 SBFL-style score 得到 `FinalScore`，输出函数级 Top-k suspicious ranking。这样做的好处是先把 LLM 的搜索空间压缩到高风险函数附近，而不是让模型读完整仓库后直接猜。

第三层是修复闭环。系统在 Top-k 函数附近生成规则、LLM 或 hybrid patch candidates，每个候选都要经过 JSON parse、AST parse、scope/signature check、safety gate 和 patch apply。只有进入 sandbox pytest 并通过目标测试，才算成功。如果补丁失败，reflection loop 会读取失败类型、旧 diff、pytest 输出和函数上下文，生成 refined candidate 并重新验证。AgentController 会把每一步的 Observe、Plan、Act、Verify、Reflect、Replan 写入 artifact。

最终我用 P6 readiness audit 做验收：10 个真实仓库 onboarding case，30 个 repair/evaluation case，5 个 LLM direct success，4 个 LLM reflection success，21 个 blocker case，24/24 readiness checks pass。边界也很明确：没有 failing test、test oracle、可复现依赖或安全修复条件时，系统输出 blocker 和下一步建议，不夸大为自动修复成功。

## 不建议写进简历的说法

- 不写“支持任意 GitHub 仓库 100% 自动修复 bug”。
- 不写“完全由大模型实现自动修复”。
- 不写“LLM judge 可以替代测试验证”。
- 不把 passing tests 场景说成“发现并修复真实 bug”。
- 不暴露任何 API key，只写通过环境变量配置 LLM provider / model。

## 推荐边界说明

当前项目定位是“面向公开 Python GitHub 仓库的智能分析与受控修复 Agent”。它可以分析仓库结构、测试环境、静态/动态证据、Top-k 定位、patch validation 或 blocker；但不承诺任意语言、任意依赖、任意真实 bug 都能自动修复。缺少 failing test、测试 oracle、可复现环境或安全修复条件时，Agent 会输出 blocker 和下一步建议。
