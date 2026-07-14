# Code Intelligence Agent V2 中文简历与面试材料

本文把 V2 的真实实现与 Phase 6/7 固定实验整理成可直接用于中文简历、项目介绍和面试追问的表达。所有量化结果都标明实验口径；不要把离线确定性 LLM fixture 写成 live 大模型在任意 GitHub 仓库上的修复率。

## 1. 项目定位

### 推荐项目名称

```text
Code Intelligence Agent：面向 Python GitHub 仓库的代码分析、缺陷定位与受控自动修复 Agent
```

### 一句话版本

```text
构建面向公开 Python GitHub 仓库的代码智能 Agent，融合 AST/Call Graph/Program Graph、多证据 Top-k 缺陷定位、LLM Planner、分层记忆与 sandbox 验证，实现 Observe -> Plan -> Act -> Verify -> Reflect -> Replan 的可审计分析和受控修复闭环。
```

### 30 秒版本

```text
我做的是一个面向公开 Python GitHub 仓库的代码智能 Agent。用户输入仓库和自然语言目标后，系统先识别工程结构与测试环境，再用 AST、调用图、程序图、静态规则、覆盖率、失败测试和 traceback 做函数级 Top-k 定位。LLM 参与意图理解、下一步规划、语义补丁和失败反思，但所有动作都必须经过 Action Registry、Schema、风险和预算门控；补丁成功只由 targeted tests 与完整 pytest sandbox 决定。系统既能输出验证修复，也能在缺少依赖、测试或证据时输出可审计 blocker。
```

## 2. 简历可直接使用的版本

### 2.1 三条标准版

```text
- 设计并实现面向公开 Python GitHub 仓库的代码智能 Agent，支持 owner/repo、URL 输入与多轮终端自然语言交互，通过 AgentController 完成 Observe -> Plan -> Act -> Verify -> Reflect -> Replan，并将每次规划、执行、验证、fallback 和 blocker 写入可审计 JSON/Markdown artifact。
- 构建 AST / Call Graph / Program Graph 与函数级多证据缺陷定位算法，融合 StaticRule、Graph、Ochiai SBFL、失败测试、动态 traceback、语义、复杂度、Git history 和受限 LLM 信号；为每个 Top-k 函数保留权重及贡献分解，并采用 validation/test/blind 仓库隔离协议防止测试集调权。
- 实现 Rule / LLM / Hybrid 自适应补丁生成、统一 AST/scope/signature/safety gate、targeted/full-regression sandbox 验证、失败 fingerprint 去重与 reflection repair；在 20 个固定 SHA 陌生公开仓库上实现 20/20 结构化报告与静态分析，完成 8 类系统消融并保留失败分母和能力边界。
```

### 2.2 偏算法岗位版

```text
- 设计函数级 evidence-fused fault localization：StaticRuleScore 使用置信度概率并集，GraphScore 融合数据依赖、控制流、PageRank、调用者影响与模块依赖，动态侧融合 Ochiai SBFL、真实 failing test 与 stack frame；输出 Top-1/3/5、MRR、MAP 及逐信号 attribution。
- 建立 repository-disjoint validation/test/blind 协议，在 15 个 validation case 上选择权重后冻结，并在 20 个 test+blind case 上评估；V2 与 V1 可比定位指标无回归，同时诚实记录移除 graph/dynamic 后指标未下降，未宣称不存在实验支持的增益。
- 设计 Top-k context、candidate budget、reflection budget 与 action budget 敏感性实验；验证 rank-3 目标在 Top-k=1 时失败、Top-k=3/5 时成功，候选预算 1/2 时失败、3 时成功，三步任务在 action budget=3 时完成。
```

### 2.3 偏大模型 Agent 岗位版

```text
- 实现 LLM Function Calling 意图路由和 LLM/Hybrid Planner，模型输出统一 Schema；控制器对动作注册、参数 allowlist、状态迁移、风险确认、重复状态和成本/时间/动作预算进行门控，provider/JSON/Schema 失败时回退规则规划器。
- 构建 Working/Session/Repo/Repair/Cross-repo Pattern 五层证据记忆，按 repo/ref、来源、置信度和有效期检索 Top-k；将用户约束、失败补丁 fingerprint 与历史验证结果注入规划和补丁上下文，支持会话恢复、去重和 commit 变化失效。
- 实现受限 reflection：读取 pytest stdout/stderr、traceback 和失败 nodeid，提取新约束并生成与历史候选实质不同的补丁；LLM Judge 仅排序，最终成功由 sandbox targeted/full regression 决定。
```

### 2.4 偏工程/平台岗位版

```text
- 实现公开 GitHub 仓库发现、src/flat/monorepo/多包布局识别、pytest/unittest/tox/nox 诊断、固定 SHA archive fallback、超时和风险策略；测试不可执行时仍完成静态分析并分类依赖、网络、权限、配置和证据 blocker。
- 设计 50+ 类可审计 artifact，覆盖 repository profile、program graph、Top-k localization、test diagnosis、patch provenance、safety gate、sandbox validation、reflection、planner resolution、memory usage 和 final report。
- 建立单元、集成、受控评估、陌生仓库盲测、release hygiene 与 clean-clone 验证，禁止 API Key、运行 outputs、缓存和二进制文档进入发布候选集。
```

### 2.5 一行压缩版

```text
开发 Python GitHub 代码智能 Agent，融合程序图、多信号 Top-k 定位、LLM Planner/Memory、Rule-LLM-Hybrid 补丁与 pytest sandbox 反思闭环，并在 20 个固定 SHA 陌生仓库及 8 类消融实验上完成可审计评估。
```

## 3. 技术栈写法

```text
Python、AST、Call Graph、Program Graph、Ochiai/SBFL、pytest、GitHub API、LLM Function Calling、JSON Schema、AgentController、Action Registry、Sandbox Validation、Evidence Memory、Beam/Candidate Search、Ablation Study
```

不要只写“大模型 API”。面试官更关心：模型在哪一层、输入是什么、输出如何校验、失败如何降级、成功由谁判定。

## 4. 可以写入简历的量化证据

| 指标 | 固定实验结果 | 正确解释 |
| --- | ---: | --- |
| 陌生公开仓库结构化报告 | 20/20 | 所有仓库都有明确终止结果 |
| 陌生仓库完成静态分析/源码根发现 | 20/20 | 不等价于测试通过或修复成功 |
| 测试命令发现 | 12/20 | 其余仓库保留缺失原因 |
| 测试进程真实启动并终止 | 7/20 | 6 个失败均分类到 environment layer |
| Test+blind localization cases | 20 | Top-1/3/5、MRR、MAP 均为 1.0 |
| Planner case/run | 14/42 | 三策略最终已注册动作率和 blocker accuracy 为 1.0 |
| Patch controlled case/run | 3/9 | Rule/LLM/Hybrid verified rate 为 0.3333/1.0/1.0 |
| Memory controlled cases | 8 | completion 0.125 到 1.0；重复失败补丁 1.0 到 0 |
| Required ablation groups | 8/8 | patch、planner、graph、dynamic、memory、reflection、Top-k、budgets |
| Phase 7 完整回归记录 | 1226 passed | 对应冻结提交的测试结果，不是线上 SLA |

### 量化表达示例

可以写：

```text
在 20 个固定 SHA、未参与开发的公开 Python 仓库上实现 20/20 结构化报告和静态分析，12/20 自动发现测试命令、7/20 真正启动并终止测试进程；其余结果按依赖、网络、配置或证据 blocker 终止，不伪造修复成功。
```

不要写：

```text
支持任意 GitHub 仓库并实现 100% 自动修复。
```

受控 patch 数字必须写出口径：

```text
在 3 个离线确定性 LLM fixture 案例上，验证 Rule/LLM/Hybrid 的候选编排、归因、安全门和 pytest 契约，verified rate 为 0.3333/1.0/1.0；该数据不作为 live 模型真实仓库修复率。
```

## 5. 两分钟项目讲解

```text
这个项目解决的是：给定一个陌生 Python GitHub 仓库，如何让 Agent 不只是总结代码，而是基于可验证证据决定下一步。

第一层是仓库理解。我识别 src、flat、monorepo 和多包布局，抽取 AST、函数调用、控制流和数据依赖，形成 Call Graph 与 Program Graph，同时诊断 pytest、unittest、tox、nox 和依赖状态。

第二层是算法定位。我把静态 finding、结构图、Ochiai 覆盖率、真实失败测试、动态 traceback、语义相似度、复杂度、Git history 和受限 LLM 分拆成独立 score，再按是否有 coverage 选择权重。每个 Top-k 函数都有 contribution，可以重建 FinalScore。

第三层是 Agent 控制。LLM 参与自然语言意图、Planner、语义补丁和失败反思，但输出必须经过 Schema。AgentController 每轮重新 Observe，模型只能推荐 Action Registry 中的动作，Safety Gate 校验参数、风险、预算和状态迁移。失败后，系统把 traceback、新约束和失败 fingerprint 写入 memory，再决定是否 Replan。

第四层是验证。Rule、LLM、Hybrid 候选统一经过 AST、scope、signature、敏感文件、测试保护和危险 API 门控，再在隔离目录执行 targeted tests 和 full regression。LLM Judge 不能决定成功。

最后我用固定 SHA 陌生仓库、repository-disjoint 定位集以及八类消融评估系统，并把成功、partial、blocker 和非法 LLM 提议全部保留在 artifact 中。
```

## 6. FinalScore 面试答法

### 6.1 公式

```text
FinalScore = clamp(
    0.22 * SBFL
  + 0.18 * Graph
  + 0.15 * StaticRule
  + 0.05 * Semantic
  + 0.05 * effective_LLM
  + 0.15 * TestFailure
  + 0.10 * StackTrace
  + 0.05 * Complexity
  + 0.05 * ChangeHistory
  - 0.05 * Risk
)
```

上式是有 coverage 的 profile。没有 coverage 时不把动态分数“猜出来”，而是切换 static-only：Graph 0.25、Static 0.45、Semantic 0.10、LLM 0.05、Complexity 0.10、History 0.05、Risk 0.05，SBFL/TestFailure/StackTrace 均为 0。

### 6.2 为什么不用纯 LLM 排名

纯 LLM 缺少真实执行状态、容易受 prompt 和上下文截断影响，也难重现。当前设计让程序证据成为主干，LLM 只补充语义信号；没有 fault-specific evidence 时 LLM contribution 直接关闭，当前权重下最大实际贡献为 0.05。

### 6.3 权重怎么得到

不是在最终测试集上调。协议使用 15 个 CPython validation case 选择四个候选 profile，冻结后在 10 个 TheAlgorithms test case 和 10 个 Click/Pluggy blind case 上评估，再与 V1 比较。当前数据集规则信号过强，fusion、without graph 和 without dynamic 都达到 1.0，所以只能说无回归，不能声称图和动态证据提升了指标。

### 6.4 一个计算例子

假设有 coverage 的函数信号为：SBFL 0.8、Graph 0.6、Static 0.7、Semantic 0.4、LLM 0.5、TestFailure 1.0、StackTrace 1.0、Complexity 0.5、History 0.2、Risk 0.4，则：

```text
0.22*0.8 + 0.18*0.6 + 0.15*0.7 + 0.05*0.4 + 0.05*0.5
+ 0.15*1.0 + 0.10*1.0 + 0.05*0.5 + 0.05*0.2 - 0.05*0.4
= 0.699
```

报告会分别保存这些 contribution，因此 0.699 可由 artifact 重建。

## 7. Rule / LLM / Hybrid 是怎么做的

### Rule

Rule patcher 把静态 finding 的 `rule_id` 映射到确定性修复模板，例如修正索引边界。优势是可复现、低成本、改动小；局限是只覆盖已编码模式。

### LLM

LLM prompt 只放 Top-k 函数、失败测试、traceback、相关图邻域、用户约束、失败 fingerprint 和输出 Schema，不把整个仓库无界塞入上下文。模型返回目标文件、函数、替换源码、理由和风险，再进入统一安全与测试链。模型没有写盘或执行命令权限。

### Hybrid

Hybrid 不是简单“先 Rule 再 LLM”。策略根据规则可修复候选数、semantic/traceback pressure、LLM 可用性和 candidate budget 选择 rule-first、llm-first 或 fallback，并记录计划预算、实际候选和 generator attribution。这样可以控制成本，也避免把规则候选通过测试归因给模型。

## 8. 为什么它是 Agent 而不只是工作流

推荐回答：

```text
底层工具当然是确定性的工作流组件，但上层不是固定顺序。控制器每轮根据 observation、用户目标、blocker、历史动作、memory 和剩余预算重新选择下一步。LLM 可以提议不同动作，规则安全控制器可以拒绝；工具执行结果又会改变下一轮状态。系统具备目标、环境观察、动作选择、真实执行、结果验证、失败反思、重规划、终止条件和跨轮记忆，因此是受控 Agent。它不是完全开放式自治系统，这是一项安全设计，而不是缺失。
```

如果面试官追问“规则多是否就不算 Agent”：

```text
Agent 性不等于所有决策都交给模型。生产 Agent 通常需要确定性 policy 约束权限和风险。这里 LLM 负责语义不确定部分，Action Registry 和 sandbox 负责不可妥协的安全与正确性边界；Phase 7 还单独比较了 rule、llm、hybrid planner，而不是只把固定 pipeline 政名为 Agent。
```

## 9. 高频面试问题与参考答案

### Q1：LLM 具体用在哪些地方？

四处：自然语言 intent Function Calling、Planner/Replanner 的下一步 proposal、规则覆盖不到的语义 patch、失败后的 reflection；可选 Judge 只做候选排序和风险审计。Repo parsing、AST、图构建、pytest 和安全门不依赖模型。

### Q2：为什么 Planner 不完全由 LLM 决定？

规划建议可以由 LLM 产生，但执行权限不能由同一个不确定组件自己授予。模型可能返回未注册动作、非法参数、错误风险或重复动作。Action Registry、Schema、状态迁移、预算和确认策略构成独立可信边界，同时保留 rule fallback 保证 provider 故障时可降级。

### Q3：能否证明 LLM Planner 真的参与了？

可以看 `planner_trace` 和 `planner_resolution`：它记录 proposal source、LLM proposed action、rule action、是否 disagreement、Safety Gate 状态、最终 adopted source、fallback、tokens 和成本。受控案例中模型提议 `delete_repository` 被拒绝，最终采用规则动作，这同时证明“参与了”和“不能绕过”。

### Q4：pytest 为什么是最终权威？

模型 Judge 只能评估文本合理性，无法替代真实运行。当前 `verified_repair` 要求目标失败测试和完整回归均通过，并通过语义安全检查。没有测试 oracle 时只输出 unverified candidate，防止把看起来正确的代码描述成已修复。

### Q5：如何防止只为通过一个测试而过拟合？

先执行 targeted test 快速筛选，再执行 full repository regression；保护测试文件、公共签名和改动范围，并支持 boundary probe 或 mutation/行为检查。即使目标测试通过，只要完整回归失败，也不会声明 verified。

### Q6：Reflection 与再次调用模型有什么区别？

Reflection 必须引入新 evidence：失败 nodeid、异常、stdout/stderr、traceback、新约束和旧候选 fingerprint。下一候选还要与旧候选有实质差异，并受轮数和宽度预算约束。没有新证据或预算耗尽就停止，不进行无限重试。

### Q7：记忆系统是否只是保存 JSON？

持久化格式是 JSON，但 Agent 能力在“结构化、检索和影响决策”。每条记录有 scope、repo/ref、来源、证据路径、置信度、验证权威和失效状态；Planner/patch 只取 Top-k 相关记忆。消融显示启用记忆后约束保留和失败 patch avoidance 改变了任务结果，而不是只多写一个文件。

### Q8：为什么暂时不用向量数据库？

当前数据主要是强结构字段，例如 repo/ref、action、rule_id、file/function、patch fingerprint 和验证状态，先做结构化过滤更可解释、更容易评估。只有当长会话或跨仓库语义召回实验显示词法/结构检索不足时，再引入 embedding，而不是为技术栈装饰增加依赖。

### Q9：任意 GitHub 仓库真的支持吗？

准确说是“面向任意公开 Python GitHub 仓库启动分析”。系统能对各种仓库输出结构化结果，但是否能跑测试和修复取决于 Python 版本、依赖、私有凭证、网络、测试 oracle 和安全权限。Phase 6 的 20 个陌生仓库中全部完成静态报告，只有 7 个真正启动并终止测试进程，这正是系统边界的实测证据。

### Q10：环境失败与代码失败怎么区分？

先解析 test collection/execution 阶段、missing module、runner、setup command、return code 和 traceback frame。ImportError、缺少插件、版本冲突和 setup failure 属于 environment layer，不能进入应用故障定位；只有执行到应用代码并产生可用失败 evidence 才启用动态定位与修复。

### Q11：如何防止危险动作？

模型只能从 Action Registry 选动作；参数逐字段校验，Shell 字符串不是通用参数。高风险环境修改需要确认，动作受状态迁移和预算限制。补丁层再检查敏感文件、测试删除、危险 API、依赖变更、范围和签名。所有拒绝都写入 trace。

### Q12：为什么 graph 消融没有下降？

当前 mutation benchmark 以规则可检测缺陷为主，StaticRuleScore 已足够把目标排在前面，所以移除 graph/dynamic 后主指标仍为 1.0。正确结论是实现无回归且消融机制有效，不是“图一定有提升”。后续应加入 static-rule-negative、跨函数根因和真实 traceback 数据集检验增量价值。

### Q13：成本如何控制？

通过 Top-k context、候选数、reflection rounds、action count、time budget 和 LLM cost budget共同控制。规则可处理时优先使用低成本候选；Planner report 记录 tokens 与估算成本。Phase 7 的 LLM/Hybrid planner fixture 各记录 1560 tokens 和 0.0156 美元配置估算，但只有费率配置与实际账单一致时才能当作 provider 成本。

### Q14：如何保证实验没有数据泄漏？

权重只用 validation split 选择，test 与 blind 不反向调参；仓库组彼此隔离。陌生仓库固定 commit SHA，受控 patch/planner fixture 固定输入和期望，报告保留 prompt/model/参数/日期和失败分母。

### Q15：项目最明显的不足是什么？

三个方面：当前定位 benchmark 规则信号偏强；Phase 7 LLM patch/planner 是确定性离线 fixture，不是多模型 live benchmark；陌生仓库测试启动率只有 7/20。后续重点应是构建更难的 semantic/cross-function 数据集、进行多模型多次 live 评估、提升环境解析与隔离安装，而不是继续堆 UI。

## 10. 深挖追问题库

面试前应能脱稿回答：

1. StaticRuleScore 为什么用概率并集而不是求和？
2. Ochiai 分母如何处理无失败测试和无覆盖？
3. GraphScore 为什么不能包含 traceback 或 coverage？
4. 三跳传播和 0.5 衰减如何避免远距离污染？
5. `effective_LLMScore` 为什么需要程序证据门控？
6. Static-only profile 为什么提高 Static 权重？
7. FinalScore clamp 后如何保持 attribution 可重建？
8. Rule patch 如何从 finding 映射到变换模板？
9. Hybrid 如何按 evidence 和预算决定生成顺序？
10. targeted pass、full regression fail 时输出什么？
11. 如何检测模型修改了测试或公共签名？
12. patch fingerprint 包含哪些稳定信息，如何去重？
13. memory 如何处理 repo commit 变化？
14. LLM JSON 解析失败与 provider network error 如何分类？
15. 已注册但高风险 action 为什么仍不能直接执行？
16. action budget 耗尽是否算失败，如何报告？
17. clean repo 为什么仍可以算一次成功的 Agent 分析？
18. 20/20 报告与 7/20 test process 的分母分别是什么？
19. 为什么 LLM Judge 不能替代 regression tests？
20. 如果没有测试 oracle，如何限制报告措辞？

详细学习答案可继续查阅 [`agent_project_study_interview_guide.md`](agent_project_study_interview_guide.md)；最新架构和评分定义以 [`architecture_and_design.md`](../v2/architecture_and_design.md) 为准。

## 11. 10 分钟项目陈述结构

1. 1 分钟：问题与目标，强调公开 Python 仓库、可审计和不伪造。
2. 2 分钟：总体架构，讲 AgentController、受控工具和 LLM 的边界。
3. 2 分钟：FinalScore、Program Graph、动态证据边界和 attribution。
4. 2 分钟：Rule/LLM/Hybrid patch、Safety Gate、pytest 与 Reflection。
5. 1 分钟：五层记忆和多轮 session。
6. 1 分钟：Phase 6/7 数据与消融，主动解释无增益结果。
7. 1 分钟：clean repo、reflection、environment blocker 和安全拒绝四种结局。

## 12. 不应出现的表述

- “任意仓库都能自动修复”。
- “LLM 自动判断补丁是否成功”。
- “Top-1 100% 说明算法泛化很好”。
- “LLM/Hybrid 在真实 GitHub 上修复率 100%”。
- “支持所有语言和所有测试框架”。
- “Agent 可以自由执行 Shell”。
- “用了向量数据库所以有长期记忆”，如果实际没有使用。

## 13. 证据索引

| 内容 | 文件 |
| --- | --- |
| 完整架构与评分 | [`docs/v2/architecture_and_design.md`](../v2/architecture_and_design.md) |
| 五类案例 | [`docs/v2/phase8_case_studies.md`](../v2/phase8_case_studies.md) |
| 现场演示 | [`docs/v2/phase8_demo_guide_cn.md`](../v2/phase8_demo_guide_cn.md) |
| Phase 6 陌生仓库 | [`docs/v2/phase6_unfamiliar_repository_robustness.md`](../v2/phase6_unfamiliar_repository_robustness.md) |
| Phase 7 总评估 | [`phase7_system_evaluation.md`](../v2/phase7_artifacts/phase7_system_evaluation.md) |
| Planner 原始结果 | [`planner_strategy_evaluation.json`](../v2/phase7_artifacts/planner_strategy_evaluation.json) |
| Patch 原始结果 | [`patch_strategy_evaluation.json`](../v2/phase7_artifacts/patch_strategy_evaluation.json) |
| Memory 原始结果 | [`memory_ablation_evaluation.json`](../v2/phase7_artifacts/memory_ablation_evaluation.json) |
| Budget 原始结果 | [`budget_ablation_evaluation.json`](../v2/phase7_artifacts/budget_ablation_evaluation.json) |
| 详细系统学习指南 | [`agent_project_study_interview_guide.md`](agent_project_study_interview_guide.md) |
