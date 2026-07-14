# Code Intelligence Agent 简历写法详细指南

> **V2 阅读提示（2026-07）**：本文保留较完整的通用简历写法，其中部分数量来自 V1。当前可直接使用且与最新 Phase 6/7 实验对齐的中文表述，请优先查看 [V2 中文简历与面试材料](v2_resume_interview_pack_cn.md)；架构、FinalScore 和能力边界以 [V2 架构与算法设计](../v2/architecture_and_design.md) 为准。

本文档用于把当前项目整理成可以写进简历、讲给面试官听、放到 GitHub 展示页中的项目经历。重点是准确表达项目价值，不夸大“任意仓库 100% 自动修复”，同时突出算法深度、Agent 特征和工程落地能力。

## 1. 项目应该如何定位

这个项目不是一个简单的“调用大模型改代码”的 demo，也不是固定顺序执行的脚本。更准确的定位是：

> 面向公开 Python GitHub 仓库的受控型代码智能 Agent，支持仓库理解、程序结构建模、函数级缺陷定位、LLM 规划、补丁生成、沙箱验证、失败反思、记忆复用和多轮终端对话。

简历上要突出三个关键词：

- 算法：AST、Call Graph、Program Graph、Top-k fault localization、SBFL-style scoring、多信号融合。
- Agent：Observe -> Plan -> Act -> Verify -> Reflect -> Replan，LLM Planner，规则安全门控，session memory，多轮对话。
- 工程：GitHub repo 输入、pytest sandbox、artifact 报告、CLI/chat-ui、评估集、测试覆盖。

不要把它写成：

- “一个代码生成工具”
- “一个普通自动化脚本”
- “一个模型 API 套壳”
- “能自动修复任意 GitHub 仓库 bug”

更好的表达是：

- “受控型代码智能 Agent”
- “代码分析与自动修复 Agent”
- “程序分析 + LLM Planner + 沙箱验证的代码智能系统”

## 2. 简历项目名称

中文推荐：

```text
Code Intelligence Agent：面向 Python GitHub 仓库的代码智能分析与受控自动修复系统
```

偏算法版本：

```text
基于程序图与多信号融合的代码智能缺陷定位与自动修复 Agent
```

偏大模型 Agent 版本：

```text
LLM Planner 驱动的代码智能分析与修复 Agent
```

偏工程平台版本：

```text
面向真实 GitHub 仓库的代码分析、测试诊断与补丁验证平台
```

英文版本：

```text
Code Intelligence Agent for Python GitHub Repository Analysis, Fault Localization and Controlled Repair
```

## 3. 一句话项目描述

中文简历推荐版本：

```text
构建面向公开 Python GitHub 仓库的代码智能 Agent，融合 AST、Call Graph、Program Graph、静态规则、动态测试证据和 LLM Planner，实现仓库理解、函数级 Top-k 缺陷定位、补丁生成、sandbox 验证、失败反思和多轮记忆式交互。
```

更偏算法版本：

```text
设计并实现基于程序图和多信号融合的函数级缺陷定位框架，将静态规则、调用图传播、动态测试证据和 SBFL-style score 融合为 Top-k suspicious ranking，并接入受控补丁生成与沙箱验证闭环。
```

更偏 Agent 版本：

```text
实现一个受控型代码智能 Agent，通过 Observe -> Plan -> Act -> Verify -> Reflect -> Replan 闭环，让 LLM Planner 参与规划，同时由 action registry、risk policy 和 sandbox gate 保留最终执行裁决。
```

更偏工程落地版本：

```text
开发支持 owner/repo、GitHub URL 和本地路径输入的代码智能分析系统，自动完成仓库发现、源码筛选、测试环境诊断、缺陷定位、补丁候选生成、pytest 沙箱验证和可审计报告输出。
```

## 4. 推荐简历 Bullet 版本

### 4.1 标准版本

适合大多数算法、后端、AI Infra、Agent 工程岗位。

```text
- 设计并实现面向公开 Python GitHub 仓库的代码智能 Agent，支持 owner/repo、GitHub URL 和本地路径输入，自动完成仓库发现、源码筛选、结构建模、测试环境诊断、函数级 Top-k 缺陷定位、补丁生成、sandbox 验证和审计报告输出。
- 构建 AST / Call Graph / Program Graph 程序结构建模模块，融合 StaticRuleScore、GraphScore、DynamicEvidenceScore、SBFL-style score 形成 FinalScore，输出可解释的 Top-k suspicious functions 与证据贡献。
- 实现 AgentController 决策闭环，将每轮 Observe -> Plan -> Act -> Verify -> Reflect -> Replan 写入 JSON/Markdown artifact，并根据测试状态、依赖缺失、LLM 失败、安全门控和 patch validation 结果自动选择下一步 action 或输出 blocker。
- 接入 LLM Planner/Replanner，使大模型在 agent-auto 模式下生成结构化 action proposal；所有 LLM 建议均经过 action registry、risk policy 和 sandbox gate 校验，未注册或证据不足的 action 会被拒绝并记录原因。
- 实现 rule / LLM / hybrid patch candidate generation、patch safety gate、pytest sandbox validation 和 reflection repair；补丁成功标准由 sandbox pytest 决定，LLM judge 仅用于候选排序和风险审计。
- 设计 session memory、repair memory 和 pattern memory，保存历史失败 patch、用户约束、测试结果和 blocker，使多轮 chat-ui 终端对话能够解释结果、切换修复策略、继续修复并避免重复失败 patch。
```

### 4.2 更强算法方向版本

如果投算法工程、程序分析、软件工程智能化方向，可以突出算法。

```text
- 设计多信号函数级缺陷定位算法，将 AST 静态规则命中、调用图邻域传播、程序图结构特征、动态测试失败证据和 SBFL-style score 融合为 FinalScore，实现 Top-k suspicious function ranking。
- 构建 Program Graph 表达函数、调用边、跨函数数据流、CFG 结构和静态缺陷信号，用于对候选函数进行图结构加权排序，并为 LLM patch generation 提供局部上下文约束。
- 将补丁生成建模为受约束搜索问题，仅在 Top-k suspicious functions 附近生成最小 diff，并通过 AST/scope/signature/safety gate 过滤越界修改，降低 LLM 随机改代码风险。
- 设计 reflection loop，将失败类型、pytest stdout/stderr、traceback、旧 diff fingerprint、目标函数源码和 caller/callee context 反馈给下一轮 refined candidate，并重新执行 sandbox validation。
- 构建真实 GitHub 仓库评估集，覆盖 onboarding、localization、repair、reflection、blocker、sandbox success、runtime 和 LLM cost 等指标，避免只在玩具样例上验证。
```

### 4.3 更强 Agent 方向版本

如果投大模型应用、Agent、AI 工具链岗位，可以突出 Agent。

```text
- 实现受控型 AgentController，将代码智能任务拆成 Observe、Plan、Act、Verify、Reflect、Replan 六个阶段，并在每轮记录 action、confidence、risk、required evidence、verification outcome 和 next plan。
- 将 LLM Planner/Replanner 接入 Plan/Replan 阶段，使模型读取仓库状态、Top-k 定位、测试结果、blocker、session memory、repair memory 和用户约束后输出结构化 action proposal。
- 保留规则安全门控：LLM 推荐 action 必须通过 action registry、risk policy 和 sandbox gate；未注册 action、危险 action、证据不足 action 不会被执行，而是作为被拒绝建议写入决策报告。
- 实现 chat-ui 终端连续聊天模式，围绕同一 repo session 支持解释 blocker、继续修复、切换修复策略、重新运行 pytest、生成报告和恢复历史 session。
- 设计分层记忆系统，保存 repo profile、Top-k suspicious functions、测试结果、patch attempt history、用户约束和历史 blocker，使下一轮规划能够避免重复失败方案。
```

### 4.4 更强工程落地方向版本

如果投后端、工具平台、DevTools、AI Infra，可以突出工程。

```text
- 实现从 GitHub repo 到可审计报告的端到端 pipeline，支持仓库发现、源码导入、缓存复用、测试命令推断、环境诊断、pytest/unittest 执行、patch validation 和 artifact inventory。
- 设计 JSON/Markdown 双格式 artifact 输出体系，包括 github_repo_intelligence、agent_controller、agent_execution_trace、agent_decision_report、agent_memory_report、repository_test_execution_result 和 final_report。
- 支持 agent-auto 自动执行模式和 chat-ui 交互模式，用户可先分析仓库，再基于 session_id 多轮追问、调整约束、继续修复或执行测试。
- 构建 release hygiene audit，检查原始 API key、误导性 LLM judge 表述、本地输出产物、Word 二进制文件和公开文档风险，降低项目发布到 GitHub 时的安全风险。
- 编写覆盖 AgentController、session memory、execution trace、repo intelligence、patch validation、LLM config audit 和报告一致性的自动化测试。
```

## 5. 精简版简历写法

如果简历空间有限，可以写成 4 条。

```text
Code Intelligence Agent：面向 Python GitHub 仓库的代码智能分析与受控自动修复系统
- 构建支持 GitHub URL / owner-repo / 本地路径输入的代码智能 Agent，自动完成仓库发现、源码筛选、结构建模、测试诊断、函数级缺陷定位、补丁生成、sandbox 验证和审计报告输出。
- 设计 AST / Call Graph / Program Graph 建模与多信号 Top-k 缺陷定位算法，融合静态规则、图传播、动态测试证据和 SBFL-style score 形成 FinalScore。
- 实现 AgentController 的 Observe -> Plan -> Act -> Verify -> Reflect -> Replan 闭环，引入 LLM Planner 生成结构化 action proposal，并通过 action registry、risk policy 和 sandbox gate 进行安全裁决。
- 设计 session/repair/pattern memory 与 chat-ui 终端连续对话，支持解释 blocker、切换修复策略、继续修复、重新运行测试并避免重复失败 patch。
```

## 6. 一页项目经历写法

如果你有项目经历单独页，可以按下面结构写。

### 项目背景

真实 GitHub 仓库的代码分析与修复比玩具样例复杂得多：仓库结构不统一，测试命令不固定，依赖环境可能缺失，很多仓库没有现成 failing test，LLM 生成的补丁也可能越界修改或无法通过测试。因此项目目标不是“让 LLM 猜一个 patch”，而是构建一个可审计、可验证、可交互的代码智能 Agent。

### 项目目标

- 输入：GitHub URL、owner/repo 或本地 Python 仓库路径。
- 输出：仓库结构分析、Top-k suspicious functions、测试诊断、补丁候选、sandbox 验证结果、Agent 决策轨迹、memory 报告和最终审计报告。
- 核心标准：补丁是否成功由 sandbox pytest 决定；LLM 只作为 Planner、Patch Generator 或 Judge，不替代执行验证。

### 技术路线

```text
GitHub Repo
  -> Repository Discovery
  -> Source Filtering
  -> AST / Call Graph / Program Graph
  -> Static Signals + Dynamic Evidence
  -> Top-k Fault Localization
  -> AgentController Plan/Replan
  -> Patch Candidate Generation
  -> Safety Gate
  -> Sandbox Validation
  -> Reflection / Memory / Report
```

### 个人工作

- 实现仓库解析与程序结构建模。
- 设计函数级缺陷定位评分体系。
- 实现 AgentController 决策闭环。
- 接入 LLM Planner 并保留规则安全门控。
- 实现 patch generation、sandbox validation、reflection loop。
- 设计 session memory 和 chat-ui 多轮对话。
- 构建报告系统和评估体系。

### 项目结果

项目现在能够对公开 Python GitHub 仓库输出完整的智能分析报告，并展示每一步是真执行、被跳过、被阻塞还是验证通过。典型 demo 覆盖：

- 正常公开 Python 仓库分析成功。
- 测试环境或动态证据 blocker。
- patch 失败后 reflection/replan。

每个 demo 都输出：

- `github_repo_intelligence.md`
- `agent_execution_trace.md`
- `agent_decision_report.md`
- `agent_memory_report.md`

## 7. STAR 面试拆解

### Situation

大模型可以生成代码，但在真实仓库中直接让 LLM 修改代码风险很高：上下文过大、仓库结构复杂、测试环境不稳定、补丁可能越界、结果不可验证。

### Task

设计一个面向真实 Python GitHub 仓库的代码智能 Agent，使它不仅能分析代码，还能定位可疑函数、生成补丁、运行 sandbox 验证，并在失败后反思和重规划。

### Action

我把系统拆成四层：

- 程序分析层：Repo parser、AST、Call Graph、Program Graph。
- 缺陷定位层：静态规则、图传播、动态测试证据、SBFL-style scoring。
- Agent 控制层：Observe -> Plan -> Act -> Verify -> Reflect -> Replan。
- 修复验证层：rule/LLM/hybrid patch generation、安全门控、pytest sandbox、reflection loop。

同时接入 LLM Planner，让模型提出下一步 action，但所有建议必须经过 action registry 和 sandbox gate。

### Result

系统能够对真实 Python GitHub 仓库生成可审计报告，并在缺少测试、依赖失败、LLM 不可用或补丁不安全时输出 blocker，而不是伪造成功。项目具备简历可讲的算法深度、Agent 特征和工程完整性。

## 8. 面试时 30 秒讲法

```text
我做的是一个面向公开 Python GitHub 仓库的代码智能 Agent。系统先做仓库发现和源码筛选，再构建 AST、Call Graph 和 Program Graph，用静态规则、图结构和测试证据计算函数级 Top-k suspicious ranking。之后 AgentController 通过 Observe -> Plan -> Act -> Verify -> Reflect -> Replan 决策下一步，是运行测试、诊断环境、生成补丁、sandbox 验证，还是输出 blocker。LLM Planner 参与规划，但最终执行由 action registry、risk policy 和 sandbox gate 控制，补丁成功标准始终是 pytest sandbox。
```

## 9. 面试时 2 分钟讲法

```text
这个项目的核心问题是：真实 GitHub 仓库不能只靠 LLM 直接改代码。因为仓库上下文大、依赖环境复杂、测试命令不一定存在，而且 LLM 生成的 patch 可能越界或无法通过测试。

所以我先做程序分析。输入 GitHub URL 后，系统会做 repo discovery 和 source filtering，然后用 AST 提取函数、类、调用、导入和规则信号，用 Call Graph 表达函数调用关系，用 Program Graph 统一表达函数节点、调用边、数据流、CFG 和静态缺陷信号。

在定位阶段，我把 StaticRuleScore、GraphScore、DynamicEvidenceScore 和 SBFL-style score 融合成 FinalScore，输出函数级 Top-k suspicious ranking。这样可以把 LLM 修复空间限制在高风险函数附近，而不是让模型读完整仓库后直接猜。

在 Agent 层，我实现了 AgentController 的 Observe -> Plan -> Act -> Verify -> Reflect -> Replan 闭环。LLM Planner 会读取仓库状态、Top-k、测试结果、blocker、session memory 和 repair memory，输出结构化 action proposal。但 LLM 不是最终执行者，所有建议都要经过 action registry、risk policy 和 sandbox gate。比如 LLM 推荐未注册 action 时，系统会拒绝并记录原因。

在修复阶段，系统支持 rule、LLM 和 hybrid patch candidate generation，每个 patch 都要经过 JSON parse、AST parse、scope/signature check、safety gate、patch apply 和 pytest sandbox。失败后 reflection loop 会读取失败类型、pytest 输出、旧 diff fingerprint 和目标函数上下文，生成 refined candidate，再重新验证。

最后系统会输出 github_repo_intelligence、agent_execution_trace、agent_decision_report 和 agent_memory_report，能回答每一步是真执行、跳过、阻塞还是验证通过。
```

## 10. 面试时 5 分钟展开顺序

建议顺序：

1. 先讲为什么真实仓库代码修复难。
2. 再讲为什么不能只靠 LLM。
3. 讲程序分析层：AST、Call Graph、Program Graph。
4. 讲定位算法：Top-k 和 FinalScore。
5. 讲 AgentController：六阶段闭环。
6. 讲 LLM Planner 和规则安全门控的分工。
7. 讲 patch validation 和 sandbox。
8. 讲 memory 和 chat-ui。
9. 讲评估和边界。

不要一上来讲模型 API，也不要先讲 UI。这个项目最强的点是“程序分析 + Agent 控制 + 可验证修复”。

## 11. 不同岗位如何调整简历重点

### 算法工程岗位

突出：

- 程序图建模。
- 多信号融合。
- Top-k ranking。
- SBFL-style score。
- 搜索和反思。
- 评估指标。

少写：

- UI。
- CLI 细节。
- 文件输出细节。

### 大模型 Agent 岗位

突出：

- LLM Planner。
- Replan。
- Memory。
- Tool/action registry。
- Safety gate。
- 多轮 chat-ui。

少写：

- 太多 pytest 参数。
- 太多 GitHub fetch 细节。

### 后端/平台岗位

突出：

- CLI pipeline。
- artifact 输出。
- session 管理。
- sandbox 执行。
- 错误处理。
- release hygiene。
- 测试覆盖。

少写：

- 过深的模型 prompt 细节。

### 软件工程/DevTools 岗位

突出：

- 真实 GitHub 仓库。
- 测试环境诊断。
- patch validation。
- regression guard。
- blocker reporting。
- 可审计报告。

## 12. 可以放在 GitHub README 的项目摘要

```text
Code Intelligence Agent 是一个面向公开 Python GitHub 仓库的代码智能分析与受控修复 Agent。用户输入 owner/repo、GitHub URL 或本地路径后，系统会自动完成仓库发现、源码筛选、AST/Call Graph/Program Graph 建模、静态缺陷信号挖掘、函数级 Top-k 缺陷定位、测试环境诊断、补丁候选生成、pytest sandbox 验证、失败反思和 blocker 报告。AgentController 采用 Observe -> Plan -> Act -> Verify -> Reflect -> Replan 闭环，LLM Planner 负责提出结构化 action proposal，规则安全门控和 sandbox validation 保留最终执行裁决。
```

## 13. 必须避免的简历表述

不要写：

```text
支持任意 GitHub 仓库自动修复所有 bug。
```

应该写：

```text
面向公开 Python GitHub 仓库进行智能分析、测试诊断、缺陷定位、受控补丁生成和 blocker 报告。
```

不要写：

```text
LLM 自动决定所有 action 并直接修改代码。
```

应该写：

```text
LLM Planner 生成 action proposal，AgentController 通过 action registry、risk policy 和 sandbox gate 进行安全裁决。
```

不要写：

```text
LLM judge 判断修复成功。
```

应该写：

```text
LLM judge 仅参与候选排序和风险审计，最终成功标准由 pytest sandbox validation 决定。
```

不要写：

```text
没有 failing test 也能可靠修复真实 bug。
```

应该写：

```text
缺少 failing test、test oracle 或可复现环境时，Agent 输出可审计 blocker 和下一步建议。
```

## 14. 项目中最值得展示的 5 个文件

运行 demo 后重点给面试官看：

- `github_repo_intelligence.md`：总报告，说明仓库、结构、测试、定位、修复和边界。
- `agent_decision_report.md`：说明 LLM 推荐了什么、Controller 采用了什么、为什么采纳或拒绝。
- `agent_execution_trace.md`：说明每一步是真执行、跳过、阻塞还是验证通过。
- `agent_memory_report.md`：说明 session/repo/repair/pattern memory 如何参与规划。
- `repository_test_patch_validation.md`：说明 patch 是否通过 sandbox。

如果只允许展示一个文件，优先展示：

```text
agent_decision_report.md
```

因为它最能证明这是 Agent，而不是普通脚本。

## 15. 简历项目结尾边界说明

如果简历允许写一行边界，可以写：

```text
系统不承诺任意仓库 100% 自动修复；当缺少 Python 源码、测试命令、依赖环境、failing evidence 或安全修复条件时，会输出 blocker 和下一步建议，避免伪造修复成功。
```

这句话反而会让项目显得更专业，因为真实工程系统必须能处理失败和不确定性。
