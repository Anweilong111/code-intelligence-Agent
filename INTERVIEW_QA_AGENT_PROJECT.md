# 代码智能 Agent 项目面试问答

## 0. 面试开场怎么讲？

30 秒版本：

我做的是一个面向公开 Python GitHub 仓库的代码智能 Agent。它不是固定顺序脚本，也不是简单调用大模型改代码，而是先把仓库解析成 AST、Call Graph 和 Program Graph，再用静态规则、图传播、动态测试证据和 SBFL-style scoring 计算函数级 Top-k suspicious ranking。AgentController 会根据当前 artifacts 和 blockers 执行 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`，决定运行测试、诊断环境、生成补丁、验证补丁、进入 reflection，或者输出 blocker。所有补丁最终都必须通过 pytest sandbox，LLM judge 不替代执行验证。

展示顺序：

1. 先讲 AgentController，证明它不是固定 workflow。
2. 再讲 AST / Call Graph / Program Graph 和 `FinalScore`，证明算法深度。
3. 再讲 patch validation、sandbox 和 reflection，证明修复不是文本猜测。
4. 最后讲 P6 矩阵和边界，证明结果可审计、不夸大。

## 1. 这个项目为什么算 Agent，而不是普通工作流？

普通工作流通常按固定顺序执行：下载仓库、分析、输出报告。这个项目有 `AgentController`，每轮都会观察当前 artifact、测试状态、定位结果和 blocker，再规划下一步 action，执行后验证结果，并根据失败原因反思和重规划。

例如：

- 没有 Python 源码时，选择 source blocker，不继续伪分析。
- 有测试但缺 runner 时，进入 environment diagnosis。
- 没有 failing test 或 oracle 时，输出 blocker 和下一步建议。
- 初始 patch 失败时，进入 LLM reflection，生成 refined candidate。
- patch 通过 sandbox 时，生成最终报告。

这就是 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`。

## 2. AgentController 的六个阶段分别做什么？

| 阶段 | 作用 |
| --- | --- |
| Observe | 读取仓库结构、测试环境、静态信号、动态证据、patch validation 和 blocker |
| Plan | 根据当前状态选择下一步 action |
| Act | 执行源码筛选、测试发现、测试运行、定位、补丁生成、sandbox 验证或 blocker 输出 |
| Verify | 检查 action 是否产生有效 artifact，是否满足阶段目标 |
| Reflect | 对失败、阻塞、无进展或 patch failure 做归因 |
| Replan | 根据归因选择继续、停止、请求外部输入或进入下一阶段 |

## 3. 代码仓库理解怎么做？

系统先做 repo discovery，解析 `owner/repo`、GitHub URL、ref、默认分支、源码候选、配置文件和测试 runner 信号。然后做 source filtering，过滤缓存、输出目录、非 Python 文件和无关内容。之后用 AST 提取函数、类、导入、调用、行号和规则信号，并输出 `repository_structure`、`repo_graph`、`analysis_readiness` 等 artifact。

## 4. AST、Call Graph、Program Graph 分别解决什么问题？

AST 解决“代码结构是什么”，例如函数边界、参数、返回、条件分支和调用表达式。

Call Graph 解决“谁调用谁”，用于跨函数风险传播。

Program Graph 把函数、调用边、静态规则、测试证据、定位分数和数据/控制流信号放到统一结构里，便于计算 `GraphScore` 和解释 Top-k ranking。

## 5. FinalScore 怎么设计？

`FinalScore` 是多信号融合分数，不依赖单一规则。它可以融合：

- `StaticRuleScore`：静态 bug pattern 先验。
- `GraphScore`：调用图或程序图上的风险传播。
- `DynamicEvidenceScore`：failing tests、traceback 和执行反馈。
- `SBFLScore`：失败/通过测试覆盖差异。
- risk penalty：越界修改、低置信度或高风险模式惩罚。

最后输出函数级 Top-k suspicious ranking，并保留每个信号的贡献。

## 6. 没有测试时系统怎么做？

没有测试、没有可执行测试命令、没有 failing evidence 或没有 oracle 时，系统不会编造动态证据。它会输出 blocker，保留静态分析结果，并给出下一步建议，例如提供 failing test、扩大 source scope、安装依赖或补充测试命令。

这点很重要，因为真实仓库经常没有现成 failing test。项目的目标是可审计分析和受控修复，而不是把不确定结果包装成成功。

## 7. patch 是怎么生成和验证的？

补丁生成只在 Top-k suspicious functions 附近进行，避免全仓库乱改。候选可能来自规则、LLM 或 hybrid 策略。每个候选都要经过：

1. JSON parse。
2. AST parse。
3. scope check。
4. signature check。
5. safety gate。
6. patch apply。
7. sandbox pytest。

只有 sandbox 执行通过，补丁才算成功。

## 8. 为什么必须 sandbox 验证？

代码补丁不能只靠文本判断。sandbox 会在隔离目录里应用 patch 并执行指定 pytest 命令，记录 return code、passed/failed/errors、stdout/stderr 和 timeout。LLM judge 可以给风险和排序建议，但不能把 sandbox fail 的候选提升为成功。

## 9. reflection loop 怎么工作？

reflection loop 会读取 parent candidate、previous diff、failure type、pytest stdout/stderr、traceback、failed patch fingerprint、target function source、caller/callee context 和可选 judge feedback，然后生成 refined candidates。refined candidates 仍然要经过 AST/scope/safety gate 和 sandbox pytest。

P6 中已经有 4 个 LLM reflection success case，其中 3 个 reflection evidence complete，用于证明失败补丁可以通过执行反馈进入下一轮修复。

## 10. 如何防止 LLM 乱改代码？

- 先用定位算法把范围压缩到 Top-k 函数。
- patch 限制在候选函数附近。
- AST/scope/signature/safety gate 阻止越界修改。
- sandbox pytest 是最终成功标准。
- 报告保留失败原因、blocker 和 caveat。
- API key 只通过环境变量注入，不写入代码或报告。

## 11. 项目的算法深度体现在哪里？

主要体现在四点：

- 结构建模：AST、Call Graph、Program Graph。
- 缺陷定位：StaticRuleScore、GraphScore、DynamicEvidenceScore、SBFLScore、FinalScore。
- 受控修复：候选生成、去重、风险过滤、sandbox validation、reflection。
- 实验评估：onboarding matrix、repair evaluation matrix、metrics report、case catalog audit、P6 readiness audit。

## 12. 当前 P6 结果是什么？

最新 P6 readiness audit 已通过：

| 指标 | 结果 |
| --- | ---: |
| Readiness checks | 24/24 pass |
| Real GitHub onboarding cases | 10 |
| Repair/evaluation cases | 30 |
| LLM direct success | 5 |
| LLM reflection success | 4 |
| LLM blocker cases | 21 |
| Declared catalog matched | 20/20 |
| Sandbox authority | `sandbox_pytest_decides_success` |

面试时要强调：这些数字证明项目具备可审计 Agent 闭环和多类别评估能力，不等于承诺任意仓库都能自动修复真实 bug。

## 13. “任意 GitHub 仓库都支持吗”怎么回答？

准确回答：

当前目标是面向公开 Python GitHub 仓库做智能分析，系统可以完成仓库发现、源码筛选、结构建模、测试诊断、Top-k 定位、可选测试执行、补丁验证和 blocker 报告。不是承诺任意语言、任意依赖、任意真实 bug 都能自动修复。对没有 Python 源码、没有测试命令、环境不可复现、缺少 failing test 或 test oracle 的仓库，Agent 会输出 blocker 和下一步建议。

## 14. LLM judge 的作用是什么？

LLM judge 参与候选排序和风险判断，输入包括 candidate summary、diff summary、localization score、safety gate summary 和 execution feedback summary。输出包括 score、verdict、reason、risk 和 confidence。

但最终成功标准仍是 sandbox pytest。也就是说，LLM judge 可以帮助排序，不能替代执行验证。

## 15. 如果继续优化，你会怎么做？

优先做三件事：

1. 增强真实仓库环境诊断，支持 uv、poetry、pdm、多包项目和更稳定的依赖安装建议。
2. 扩展真实 issue / PR diff / failing tests 到可复现实验样例的自动转化能力。
3. 增强跨文件数据流和更复杂调用链下的定位与修复能力。

## 16. 项目有哪些边界？

- 主要面向 Python 仓库。
- 不承诺任意真实 bug 都能自动发现和修复。
- 不承诺任意 GitHub 仓库都能 100% 自动修复真实 bug。
- 没有 failing evidence 或 test oracle 时不能声称修复成功。
- 复杂系统依赖、私有服务、超大型 monorepo 仍需要更多工程化。
- LLM 输出必须经过结构化解析、安全门禁和 sandbox 验证。
