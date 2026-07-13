# 代码智能 Agent 项目面试问答

## 0. 面试开场怎么讲

30 秒版本：

我做的是一个面向公开 Python GitHub 仓库的代码智能 Agent。它不是固定顺序脚本，也不是简单调用大模型改代码，而是先把仓库解析成 AST、Call Graph 和 Program Graph，再用静态规则、图传播、动态测试证据和 SBFL-style scoring 计算函数级 Top-k suspicious ranking。AgentController 会根据当前 artifacts 和 blockers 执行 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`，决定运行测试、诊断环境、生成补丁、验证补丁、进入 reflection，或者输出 blocker。所有补丁最终都必须通过 pytest sandbox，LLM judge 不替代执行验证。

展示顺序：

1. 先讲 AgentController，证明它不是固定 workflow。
2. 再讲 AST / Call Graph / Program Graph 和 `FinalScore`，证明算法深度。
3. 再讲 patch validation、sandbox 和 reflection，证明修复不是文本猜测。
4. 最后讲 V1 评估和边界，证明结果可审计、不夸大。

## 1. 这个项目为什么是 Agent，而不是普通工作流？

普通工作流通常按固定顺序执行：拉取仓库、分析、测试、报告。这个项目的 `AgentController` 每一轮都会观察当前 artifact、测试状态、定位结果和 blocker，再规划下一步 action，执行后验证结果，如果遇到失败原因就反思并重规划。

例如：

- 没有 Python 源码时，选择 source blocker，而不是伪造分析结果。
- 有测试但缺 runner 或依赖时，进入 environment diagnosis。
- 没有 failing test 或 oracle 时，输出 blocker 和下一步建议。
- 初始 patch 失败时，进入 LLM reflection，生成 refined candidate。
- patch 通过 sandbox 时，输出最终报告。

这就是 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`。

## 2. AgentController 六个阶段分别做什么？

| 阶段 | 作用 |
| --- | --- |
| Observe | 读取仓库结构、测试环境、静态信号、动态证据、patch validation 和 blocker |
| Plan | 根据当前状态选择下一步 action |
| Act | 执行源码筛选、测试发现、测试运行、定位、补丁生成、sandbox 验证或 blocker 输出 |
| Verify | 检查 action 是否产出有效 artifact，是否满足阶段目标 |
| Reflect | 对失败、阻塞、无进展或 patch failure 做归因 |
| Replan | 根据归因选择继续、停止、等待外部条件或进入下一阶段 |

## 3. 任意 GitHub 仓库输入后怎么处理？

系统先做 repo discovery，支持 `owner/repo`、GitHub URL、ref、默认分支、源码候选、配置文件和测试 runner 信号。然后做 source filtering，过滤缓存、构建目录、非 Python 文件和无关数据。之后用 AST 提取函数、类、导入、调用、行号和规则信号，并生成 `repository_profile`、`repository_structure`、`repo_graph`、`analysis_readiness` 等 artifact。

## 4. AST、Call Graph、Program Graph 分别解决什么问题？

AST 解决“代码结构是什么”，例如函数边界、参数、返回、异常分支和调用表达式。

Call Graph 解决“谁调用谁”，用于跨函数风险传播。

Program Graph 把函数、调用边、静态规则、测试证据、定位分数、修复候选和验证信号放到统一结构里，便于计算 `GraphScore` 和解释 Top-k ranking。

## 5. FinalScore 怎么设计？

`FinalScore` 是多信号融合分数，不是单一规则：

- `StaticRuleScore`：静态 bug pattern 命中。
- `GraphScore`：调用图和程序图上的风险传播。
- `DynamicEvidenceScore`：failing tests、traceback 和执行反馈。
- `SBFLScore`：失败/通过测试覆盖差异。
- risk penalty：越界修改、低置信度或高风险模式惩罚。

输出不是只有 Top-k suspicious ranking，还会记录每个信号的贡献。

## 6. 没有测试时系统怎么处理？

没有测试、没有可执行测试命令、没有 failing evidence 或没有 oracle 时，系统不会伪造动态证据，也不会声称自动修复成功。它会输出 blocker、静态分析结果和下一步建议，例如提供 failing test、扩大 source scope、安装依赖或补充测试命令。

这很重要，因为真实仓库经常没有现成 failing test。项目目标是可审计分析与受控修复，而不是把不确定结果包装成成功。

## 7. patch 是怎么生成和验证的？

补丁只在 Top-k suspicious functions 附近生成，不全仓库乱改。候选可以来自规则、LLM 或 hybrid 策略。每个候选要经过：

1. JSON parse
2. AST parse
3. scope check
4. signature check
5. safety gate
6. patch apply
7. sandbox pytest

只有 sandbox 执行通过，才算修复成功。

## 8. 为什么强调 sandbox 验证？

代码补丁不能只靠文本判断。sandbox 会在隔离目录里应用 patch 并执行指定 pytest 命令，记录 return code、passed/failed/errors、stdout/stderr 和 timeout。LLM judge 可以辅助排序和解释，但不能把 sandbox fail 的候选判为成功。

## 9. reflection loop 怎么设计？

reflection loop 会读取 parent candidate、previous diff、failure type、pytest stdout/stderr、traceback、failed patch fingerprint、target function source、caller/callee context 和可选 judge feedback，然后生成 refined candidates。refined candidates 仍然要经过 AST/scope/safety gate 和 sandbox pytest。

V1 评估中 `reflection_uplift` 已作为 measured metric 进入 evaluation summary，当前值为 0.1333，表示 reflection 在 repair case 上贡献了额外成功率。

## 10. 如何防止 LLM 乱改代码？

- 用定位算法把范围压缩到 Top-k 函数。
- patch 生成限制在候选函数附近。
- AST/scope/signature/safety gate 防止越界修改。
- sandbox pytest 作为最终成功标准。
- 报告保留失败原因、blocker 和 caveat。
- API key 只通过环境变量注入，不写入代码或报告。

## 11. 项目的算法深度体现在哪里？

主要体现在四点：

- 结构建模：AST、Call Graph、Program Graph。
- 缺陷定位：StaticRuleScore、GraphScore、DynamicEvidenceScore、SBFLScore、FinalScore。
- 受控修复：候选生成、去重、风险过滤、sandbox validation、reflection。
- 真实评估：onboarding matrix、repair evaluation matrix、metrics report、case catalog audit、V1 evaluation summary。

## 12. 当前 V1 评估证明了什么？

当前 V1 evidence snapshot：

| 指标 | 结果 |
| --- | ---: |
| GitHub onboarding repositories | 30/30 |
| Repair/evaluation cases | 50 |
| Required metric contracts | 9/9 |
| Directly measured metrics | 9/9 |
| Proxy metrics | 0 |
| Missing evidence metrics | 0 |

9 个指标包括：onboarding success、Top-k localization、Pass@1、Pass@k、reflection uplift、blocker accuracy、sandbox success、average runtime 和 LLM cost。

## 13. 还有哪些边界？

项目不承诺任意真实 bug 都能自动修复。以下情况会输出 blocker：

- 仓库不是 Python 项目或源码不可解析。
- 没有测试、没有 failing test 或没有 oracle。
- 依赖缺失、安装失败或 pytest 超时。
- LLM API key 缺失或 provider 失败。
- 补丁不安全、越界修改或 sandbox fail。

这些 blocker 是系统设计的一部分，不是失败隐藏。

## 14. 面试官问“为什么不用纯 LLM”怎么回答？

纯 LLM 的问题是上下文大、搜索空间大、容易越界修改，而且很难证明补丁正确。这个项目先用程序分析把问题压缩到函数级 Top-k，再让 LLM 在受约束上下文中生成候选，最后用 AST/scope/safety gate 和 sandbox pytest 验证。也就是说，LLM 是 Agent 的一个 action，不是唯一决策者。

## 15. 最适合写在简历上的总结

面向任意公开 Python GitHub 仓库的代码智能 Agent：融合静态程序分析、图结构建模、LLM 自动修复、沙箱验证与反思式规划，实现从仓库理解到缺陷定位和补丁验证的端到端智能体系统。
## LLM Planner Planning Authority

Accurate interview wording: the LLM is the default planning proposer in
`agent-auto` mode, not an unrestricted executor. The planner reads repository
state, blockers, Top-k localization, test results, session memory, repair
memory, failed patch fingerprints, user constraints, and repair strategy
preferences. It outputs `selected_action`, `reason`, `confidence`, `risk`,
`required_evidence`, `next_plan`, and `memory_used`.

The final execution decision still belongs to AgentController through the
action registry, risk policy, and sandbox safety gate. If the LLM is unavailable
or the replan key is not configured, the report records
`fallback_to_rule_planner=true`, keeps the rule-selected action, and explains
which action was proposed, which action was adopted, and why.
