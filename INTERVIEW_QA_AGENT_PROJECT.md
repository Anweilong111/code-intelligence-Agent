# 代码智能 Agent 项目面试问答

## 0. 面试开场怎么讲？

建议按 30 秒版本开场：

我做的是一个面向公开 Python GitHub 仓库的代码智能 Agent。它不是固定顺序脚本，也不是简单调用大模型改代码，而是先把仓库解析成 AST、Call Graph 和 Program Graph，再用静态规则、图传播、动态测试证据和 SBFL 计算函数级 Top-k suspicious ranking。AgentController 会根据当前 artifact 和 blocker 执行 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`：能测试就运行测试，环境缺 runner 就输出环境修复建议，测试全通过就记录 regression guard，补丁失败就进入 reflection 生成 refined candidate。最后所有结论都会落到 JSON/Markdown 报告里，方便审计。

展示顺序建议：

1. 先讲 `AgentController`，证明它不是普通 workflow。
2. 再讲 AST / Call Graph / Program Graph 和 `FinalScore`，证明算法深度。
3. 再讲 pytest sandbox 和 reflection，证明补丁不是文本猜测。
4. 最后讲 5 仓库 acceptance suite、9 仓库 P3 鲁棒性矩阵和能力边界，证明项目可信但不夸大。

## 1. 这个项目为什么算 Agent，而不是普通工作流？

普通工作流通常是固定顺序：下载仓库、分析、输出报告。这个项目加入了 `AgentController`，每一轮都会先观察当前 artifact 和 blocker，再规划下一步动作，执行后验证结果，并根据失败原因反思和重规划。也就是 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`。例如测试全部通过时，它不会强行修复，而是转成 regression guard；没有 Python 源码时，它输出 source blocker；补丁失败时，它进入 reflection 生成 refined candidate。

## 2. AgentController 的六个阶段分别做什么？

`Observe` 读取仓库结构、静态信号、测试结果、patch validation 和 blocker。`Plan` 根据当前阶段选择下一步动作。`Act` 执行源码扩展、测试发现、测试运行、补丁生成或验证。`Verify` 检查动作是否产生新 artifact 或是否达到阶段目标。`Reflect` 对失败、阻塞或无进展做归因。`Replan` 根据归因选择继续、停止、要求外部输入或进入下一阶段。

## 3. 你怎么做代码仓库理解？

系统先做 GitHub repo discovery，解析 `owner/repo`、URL、ref、源码列表和配置文件。然后通过 source filtering 去掉输出目录、缓存、测试无关文件和不支持语言，保留 Python 源码。之后用 AST 提取函数、类、导入、调用、行号和规则信号，并输出 `repository_structure`、`repo_graph`、`analysis_readiness` 等报告。

## 4. AST、Call Graph、Program Graph 分别解决什么问题？

AST 解决“代码结构是什么”，例如函数边界、参数、返回、调用表达式和条件分支。Call Graph 解决“谁调用谁”，用于跨函数传播风险。Program Graph 是更统一的表示，把 AST 节点、函数、调用边、规则信号、动态测试证据和定位分数放到同一张图里，方便做 GraphScore 和可解释 Top-k 排名。

## 5. FinalScore 怎么设计？

`FinalScore` 是多信号融合分数，不依赖单一规则。它可以融合 `StaticRuleScore`、`GraphScore`、`DynamicEvidenceScore`、`SBFLScore` 和风险惩罚。静态规则提供 bug pattern 先验，GraphScore 提供调用和结构传播，动态证据来自 failing tests 或 traceback，SBFL 来自通过/失败测试覆盖差异。最后输出函数级 Top-k suspicious ranking。

## 6. SBFL 和 GraphScore 有什么区别？

SBFL 依赖测试覆盖和失败/通过分布，核心问题是“哪些代码更常被失败测试覆盖”。GraphScore 依赖程序结构，核心问题是“哪些函数在调用图、依赖图或数据/控制流上更接近风险信号”。当有动态测试覆盖时 SBFL 很有价值；当测试不可用或没有失败测试时，GraphScore 和静态规则能提供 fallback。

## 7. 没有测试时系统怎么做？

没有测试或没有可执行测试命令时，系统不会编造动态证据，而是输出 blocker，并保留静态分析结果。AgentController 会选择类似 `expand_static_candidate_search`、`adjust_source_filters` 或要求用户提供 failing test / bug report 的动作。这样可以保证报告可审计，不把不确定结论包装成修复成功。

## 8. 有测试失败时怎么利用动态证据？

系统会解析 pytest stdout/stderr、失败测试名、失败类型、traceback、断言差异和测试统计，把这些信息写入 `repository_test_dynamic_evidence`。如果失败测试能映射到应用函数，就进入动态 fault localization；如果不能完全匹配，也会记录 unmatched evidence，并结合静态图和规则分数输出排名。

## 9. patch 是怎么生成的？

当前项目支持规则式 patch generation，并预留 LLM patch provider 配置。生成器只在 Top-k suspicious functions 附近产生候选补丁，避免全仓库乱改。补丁会经过 AST 可解析性、函数范围、scope、风险和 safety gate 检查，然后才进入 sandbox 验证。

## 10. 为什么必须 sandbox 验证？

代码补丁不能只靠文本判断。sandbox 会在隔离目录里应用 patch 并执行指定 pytest 命令，记录 return code、passed/failed/errors、stdout/stderr 和 timeout。只有执行证据通过，补丁才算成功；否则进入失败归因或 reflection。

## 11. reflection loop 怎么工作？

reflection loop 会读取失败补丁、失败类型、测试输出、旧 diff fingerprint 和候选函数上下文，然后生成下一轮 refined candidate。TheAlgorithms/Python gronsfeld case 中，depth=0 候选失败，depth=1 reflection candidate 增加 `if not key_len: return 0`，最终通过目标 pytest。

## 12. 如何防止 LLM 或规则乱改代码？

第一，定位阶段先缩小到 Top-k 函数。第二，patch 生成限制在候选函数附近。第三，用 AST/scope/safety gate 检查 diff 是否越界、是否语法可解析、是否风险过高。第四，用 sandbox 执行测试。第五，报告会保留失败和 caveat，不把不确定补丁标成成功。

## 13. 项目的算法深度体现在哪里？

主要体现在四点：结构建模、缺陷定位、补丁搜索、实验评估。结构建模包括 AST、Call Graph 和 Program Graph。缺陷定位包括 StaticRuleScore、GraphScore、DynamicEvidenceScore、SBFLScore 和 FinalScore。补丁搜索包括候选生成、风险过滤、sandbox validation 和 reflection。实验评估包括 acceptance suite、ablation-style evaluation、quality gate 和可审计 artifacts。

## 14. 当前项目有什么不足？

当前主要面向 Python 仓库。复杂依赖、私有服务、系统级依赖、超大型 monorepo、无测试 oracle 的真实 bug 自动发现仍然需要更多工程化。项目可以分析任意公开 Python GitHub repo 并输出报告，但不能承诺任意仓库都能自动发现真实 bug、生成 ground truth 并 100% 修复。

## 15. 如果继续优化，你会怎么做？

我会优先做三件事。第一，增强真实仓库环境诊断，支持 tox、nox、uv、poetry、多包项目和更稳定的依赖安装建议。第二，引入更强的 LLM patch/judge provider，但保持 sandbox 和 safety gate 作为最终约束。第三，扩展 benchmark 自动化，把更多真实 issue、PR diff、failing tests 转成可复现实验样例。

## 16. 为什么不直接让大模型读整个仓库？

大模型读整个仓库成本高、上下文有限，而且容易改错位置。程序分析可以先把仓库压缩成结构化事实：哪些函数存在、谁调用谁、哪些测试失败、哪些函数有规则风险。LLM 更适合作为候选解释和 patch 生成器，而不是唯一决策者。

## 17. passing tests 场景为什么也有价值？

真实仓库经常没有现成失败测试。passing tests 场景说明 Agent 能识别“当前没有可用动态失败证据”，并把测试结果转成 regression guard。它不会为了展示效果而虚构 bug，这体现了可审计和工程可靠性。

## 18. 面试官问“任意 GitHub 仓库都支持吗”怎么回答？

准确回答是：当前目标是面向公开 Python GitHub 仓库做智能分析，能够完成仓库发现、源码筛选、结构建模、测试诊断、Top-k 定位、可选测试执行、补丁验证和 blocker 报告。不是承诺任意语言、任意依赖、任意真实 bug 都能自动修复。对没有 Python 源码、没有测试或环境不可复现的仓库，Agent 会输出 blocker 和下一步建议。

## 19. P3 9 仓库矩阵证明了什么？

P3 矩阵不是为了证明 Agent 能自动修复每一个公开仓库。它证明的是：系统能接收多种公开 Python GitHub 仓库输入，完成输入归一化、discovery cache 复用、source limit、源码筛选、仓库结构建模、Program Graph 构建、测试/环境诊断，并且对 progress 和 blocker 都输出可审计的 AgentController 决策链。

最重要的数字是：9/9 agent passed、9/9 objective compliance、9/9 AgentController loop complete、8/9 repo graph ready、1 个 source-import blocker、1 个 no-test-command blocker、1 个经过验证的 patch/reflection repair path。面试时要把它表述为鲁棒性矩阵，目标是广泛分析和 blocker 报告；任意真实 bug 自动修复仍然需要可复现 failing evidence 或 test oracle。

## 20. 为什么 P3 矩阵仍然是 Agent，而不是 workflow？

因为每次运行都会生成 `github_repo_agent_controller.json/md`，记录 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`。对于大仓库，P3 矩阵故意使用轻量 controller planning，而不是对每个仓库强行进入多步修复。这是 Agent 设计选择：controller 会观察预算、blocker、testability 和 repairability，再决定是继续测试、扩展源码、调整过滤器、进入补丁验证，还是输出可审计 blocker。固定 workflow 则会无视证据，对每个仓库都尝试同样步骤。

## 21. 用户输入任意公开 Python GitHub 仓库后发生什么？

系统首先规范化输入，支持 `owner/repo` 和 GitHub URL。然后执行 repo discovery，确定 ref、默认分支、源码候选、配置文件和测试 runner 信号。接着进行 source filtering，只保留可分析的 Python 源码，再用 AST / Call Graph / Program Graph 建模函数、类、调用、规则信号和结构关系。之后 AgentController 读取当前 artifact，判断能否进入测试执行、动态证据融合、补丁生成或 blocker 报告。

准确边界是：对于公开 Python GitHub 仓库，系统可以输出智能分析报告和下一步动作；对于没有 Python 源码、没有测试命令、环境不可复现、缺少 failing test 的仓库，它会输出 blocker 和恢复建议，而不是宣称修复成功。
