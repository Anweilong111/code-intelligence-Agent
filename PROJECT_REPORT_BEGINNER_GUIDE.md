# Code Intelligence Agent System 项目完整报告与学习指南

> 面向读者：刚开始学习代码智能体、程序分析、自动修复、LLM Agent 的同学。  
> 阅读目标：不看源码也能理解这个项目每一步怎样做、为什么这样做、做完后会得到什么结果，并能把项目准确写进简历、在面试中讲清楚。

## 0. 先给结论

本项目是一个面向 Python 代码仓库的代码智能体原型。它不是简单调用大模型生成代码，而是把程序分析、图推理、缺陷定位、补丁搜索、沙箱验证和实验评估串成可复现闭环。

一句话理解：

> 先用程序分析把仓库变成结构化图，再用定位算法找出最可疑函数，然后在受控范围内搜索补丁，最后用 pytest sandbox 验证补丁，并用 benchmark 与 ablation 证明每个模块确实有贡献。

![图 1：端到端代码智能体流程总览](project_report_diagrams/architecture.png)

当前可以准确表达的能力：

| 能力 | 可以怎么说 | 不要夸大成 |
| --- | --- | --- |
| 仓库理解 | 支持 Python 文件解析、函数/类/导入/调用关系建模 | 理解所有语言和所有框架 |
| 图算法定位 | 构建 Program Graph，融合 SBFL、GraphScore、StaticRuleScore、Semantic/LLMScore 做 Top-k 排序 | 只靠大模型猜 bug 位置 |
| 自动修复 | 在 Top-k 函数内生成候选补丁，经过 AST/scope/risk 校验后进入 sandbox | 任意仓库都能自动提交 PR |
| 评估闭环 | 有 benchmark runner、metrics、ablation、quality gate、showcase report | 没有 ground truth 也能评估真实 bug |
| GitHub 数据 | 支持 GitHub raw-source mutation benchmark 和 source cache | 任意 GitHub URL 一键真实 bug benchmark 化已完全完成 |

当前 README 记录的展示指标：

| 指标 | 当前结果 | 怎么理解 |
| --- | ---: | --- |
| Benchmark Cases | 62 | 当前 showcase 使用 62 个 cross-repo mutation cases |
| Top-1 Localization | 1.0000 | 正确缺陷函数排第一的比例 |
| Top-3 Localization | 1.0000 | 正确缺陷函数进入前三的比例 |
| Patch Success Rate | 1.0000 | 生成补丁后测试通过的比例 |
| Beam Success Rate | 0.9516 | Beam Search 在候选搜索中找到成功补丁的比例 |
| Source Groups | 4 | 覆盖 CPython、TheAlgorithms/Python、pytest-dev/pluggy、pallets/click 等来源 |

需要准确说明的边界：

- 已完成：GitHub raw-source mutation benchmark、template/materializer/runner、定位、修复、sandbox、报告、消融、showcase。
- 正在深化：任意 GitHub repo URL 一键自动识别真实 bug、生成 failing tests、构造 ground truth、自动进入 benchmark。
- 不建议写：支持任意 GitHub 仓库一键自动修复并提交 PR。

## 1. 初学者必须先理解的核心概念

### 1.1 什么是代码智能体

普通 LLM 代码助手通常是：用户描述问题，大模型直接给出修改建议，用户自己判断是否正确。

代码智能体更进一步：它会读取仓库、理解结构、定位问题、生成补丁、执行测试、根据失败反馈继续修复，并输出可验证结果。

为什么要这样做：

- 代码修复不是只靠语言理解就能完成，必须知道函数边界、调用关系和测试结果。
- LLM 可能改错函数、生成过大的 diff、破坏已有逻辑。
- 程序分析可以约束 LLM，让它只在高可疑位置做最小修改。
- sandbox 测试可以把“看起来对”变成“执行后确实对”。

### 1.2 核心概念速查

| 概念 | 它是什么 | 为什么重要 | 项目中怎么用 |
| --- | --- | --- | --- |
| AST | 把代码变成语法树，精确表示函数、变量、调用、条件、返回值 | 字符串搜索不理解代码结构 | 规则检测、函数提取、补丁校验 |
| CFG | 描述函数内部的执行路径，如 if、for、try | bug 常常只在某个分支或异常路径出现 | branch/path coverage、控制流信号 |
| Call Graph | 描述函数之间谁调用谁 | 测试失败入口不一定是 bug 所在函数 | 调用链追踪、caller impact |
| Data-flow | 描述变量值如何从输入、赋值、参数流到下游 | 空列表、缺失 key、None 等 bug 常靠数据传播触发 | data dependency、slice evidence |
| SBFL | 根据失败/通过测试覆盖计算可疑度 | 失败测试独有覆盖更可疑 | FinalScore 的测试证据 |
| Program Graph | 把 AST、CFG、Call Graph、Data-flow、测试覆盖放进同一张异构图 | 让定位结果可解释、可传播、可消融 | GraphScore、slice-grounding |
| Sandbox | 在隔离目录运行 pytest 验证补丁 | 自动修复必须有执行证据 | patch success、failure feedback |
| Ablation | 关闭一个模块重新评估 | 证明算法模块真的有贡献 | without SBFL、without Beam Search 等 |

![图 2：Program Graph 的证据来源](project_report_diagrams/program_graph.png)

## 2. 总体架构：系统从输入到输出怎样工作

系统执行顺序可以理解为八步：

| 步骤 | 系统具体做什么 | 为什么这样做 | 做完后的结果 |
| --- | --- | --- | --- |
| 1. 读取输入 | 接收 repo、failing tests、benchmark template 或 GitHub raw source | 明确分析对象和验证方式 | 得到待分析项目 |
| 2. 解析仓库 | 扫描 Python 文件，提取函数、类、导入、调用 | 后续定位必须知道代码结构 | 得到函数表、文件表、调用边 |
| 3. 构建 Program Graph | 合并 AST、CFG、Call Graph、Data-flow、pytest trace | 单一视角不足以定位复杂缺陷 | 得到异构程序图 |
| 4. 缺陷定位 | 计算 SBFL、GraphScore、StaticRuleScore、Semantic/LLMScore | 多信号融合比单一信号更稳 | 得到 Top-k suspicious functions |
| 5. 生成补丁 | 在 Top-k 函数内生成多个候选补丁 | 限制修改范围，降低误改概率 | 得到 patch candidates |
| 6. 补丁校验 | 检查 AST 可解析、函数边界、签名、风险 | 防止坏补丁进入执行阶段 | 过滤无效或高风险补丁 |
| 7. 沙箱验证 | 复制到隔离目录运行 pytest，收集 stdout/stderr/traceback | 真实执行是补丁成功的证据 | 得到 success/failure/timeout |
| 8. 反思修复 | 根据失败类型和执行反馈继续生成 refined patch | 第一轮补丁经常不够准确 | 得到更好的补丁或失败报告 |

做完后的最终输出通常包含：

- 可疑函数 Top-k 排名。
- 每个函数的定位信号分解。
- 最佳补丁候选和风险解释。
- pytest sandbox 执行结果。
- reflection 轮数和失败归因。
- benchmark 指标、消融实验、quality gate、showcase report。

## 3. Phase 1：仓库理解与单文件静态分析

### 3.1 Phase 1 的目标

Phase 1 的目标是先把代码读懂。没有这一步，后面的定位和修复只能靠文本猜测。

核心交付：

- Repo Parser：读取文件和函数。
- AST Analyzer：分析语法结构。
- Call Graph：建立调用关系。
- Rule-based Bug Detector：识别常见 bug 模式。
- 基础测试：确保单文件和小型项目能稳定解析。

### 3.2 每一步具体怎样做

| 步骤 | 具体怎么做 | 为什么这样做 | 做完后的结果 |
| --- | --- | --- | --- |
| 读取仓库 | 扫描 Python 文件，过滤缓存、输出目录和无关文件 | 避免把 benchmark cache 或生成文件误当成源码 | 得到待分析文件列表 |
| 解析 AST | 对每个文件构建语法树，提取函数、类、语句范围 | 缺陷定位必须知道函数边界和源码范围 | 得到函数表、类表、行号、源码片段 |
| 收集调用 | 识别直接调用、模块别名、from import、类方法调用 | 多文件项目里 bug 往往藏在被调用函数中 | 得到函数到函数的调用边 |
| 识别规则 | 用 AST 规则识别边界、类型、API misuse、mutable state 等风险 | 静态规则可以提供强先验，减少 LLM 盲猜 | 得到 rule finding 和候选函数 |
| 过滤误报 | 对容易误报的规则做上下文判断 | 算法项目不能只追求 recall，也要控制 precision | 减少无关告警 |

### 3.3 举例说明

假设项目中有一个“计算平均值”的函数，失败测试传入空列表。Phase 1 会做这些事：

1. 找到这个函数所在文件。
2. 识别函数名、参数、返回表达式和行号。
3. 判断函数内部是否存在空输入保护。
4. 找到哪些测试或业务函数调用了它。
5. 如果发现缺少边界保护，就生成一个静态规则 finding。

Phase 1 的结果不是最终补丁，而是结构化事实：哪些函数存在、它们在哪里、它们怎样互相调用、哪些位置初步可疑。

## 4. Phase 2：函数级缺陷定位

### 4.1 Phase 2 的目标

Phase 2 解决的问题是：测试失败后，应该优先怀疑哪个函数？

这是项目算法深度最核心的部分，因为它把“全仓库搜索”压缩为“Top-k 可疑函数搜索”。

![图 3：FinalScore 融合多个定位证据](project_report_diagrams/score_fusion.png)

### 4.2 FinalScore 由哪些信号组成

| 定位信号 | 怎么计算 | 为什么有用 | 输出例子 |
| --- | --- | --- | --- |
| SBFL | 统计失败测试和通过测试覆盖某函数的情况 | 失败测试独有覆盖更可疑 | 函数 A: 0.72 |
| GraphScore | 利用调用距离、PageRank、caller impact、module dependency、async call、data/control flow | bug 可能沿调用链或数据流传播 | 函数 A: 0.81 |
| StaticRuleScore | 把 AST 规则命中转成函数级先验 | 缺少边界检查、错误 API 用法等本身就是风险 | 函数 A: 0.60 |
| SemanticSimilarity | 比较失败测试名、报错信息和函数语义 | 错误信息常包含业务含义 | 函数 A: 0.75 |
| LLMScore | 可选：让大模型基于候选函数和失败上下文打分 | 补充语义推理，但不替代程序证据 | 函数 A: 0.90 |
| Risk Penalty | 根据补丁风险、影响 caller、跨文件变更等扣分 | 避免优先修改高风险核心函数 | 函数 A: -0.20 |

### 4.3 Phase 2 的完整流程

1. 把 Phase 1 的函数、调用、规则发现和测试覆盖合并到 Program Graph。
2. 为每个函数计算 SBFL、GraphScore、StaticRuleScore、Semantic/LLMScore 等分量。
3. 按配置权重融合为 FinalScore。
4. 输出 Top-k suspicious functions。
5. 保留每个分量的解释，让报告能回答“为什么它排第一”。
6. 用 attribution/counterfactual 分析解释：如果去掉某个信号，排名是否会变化。

### 4.4 举例说明

假设失败测试名是“test_empty_average”，错误信息和空输入有关。系统可能看到：

- 函数 A 被失败测试覆盖，SBFL 较高。
- 函数 A 位于失败调用链上，GraphScore 较高。
- 函数 A 命中了缺少空输入保护规则，StaticRuleScore 较高。
- 函数 A 的函数名和失败测试语义接近，SemanticSimilarity 较高。

因此函数 A 会排在 Top-1。报告不是只给出结论，而是列出每个信号的贡献。

## 5. Phase 3：自动修复闭环

### 5.1 Phase 3 的目标

Phase 3 解决的问题是：定位到可疑函数后，如何生成补丁，并证明补丁真的修好了问题。

![图 4：补丁搜索、sandbox 和 reflection loop](project_report_diagrams/repair_loop.png)

### 5.2 每一步具体怎样做

| 步骤 | 怎么做 | 为什么 | 结果 |
| --- | --- | --- | --- |
| 候选生成 | 只在 Top-k 可疑函数内生成多个最小补丁 | 限制修改范围，降低误改概率 | 得到 patch candidates |
| AST 校验 | 检查补丁后函数仍能解析、函数名/签名/缩进范围合理 | 防止 LLM 生成语法坏代码或大范围重写 | 过滤无效补丁 |
| 风险评估 | 统计 diff 大小、影响 caller、data-flow fanout、跨文件影响 | 越高风险越不应该优先执行 | 得到 PatchRisk |
| Sandbox 执行 | 复制到隔离目录运行 pytest，设置超时并收集 stdout/stderr/traceback | 真实执行是自动修复的最终证据 | 得到 passed/failed/timeout |
| 失败归因 | 把失败分成 assertion、runtime、syntax、import、patch apply、timeout 等 | 不同失败类型需要不同 refine 策略 | 得到 execution_feedback |
| Reflection | 把失败反馈、旧 diff、调用上下文和失败 fingerprint 送回生成器 | 让下一轮避免重复失败补丁 | 得到 refined candidates |

### 5.3 举例说明

假设第一轮补丁只是简单返回默认值，结果导致另一个测试失败。系统不会直接接受这个补丁，而是记录：

- 哪个测试失败。
- 断言差异是什么。
- 补丁修改了哪些行。
- 失败补丁的 fingerprint 是什么。
- 这个失败是否可恢复。

第二轮生成器会看到“这个策略已经失败”，于是尝试更小范围的边界保护。只有 pytest 全部通过，补丁才进入最终报告。

## 6. Phase 4：搜索增强与实验评估

### 6.1 Phase 4 的目标

Phase 4 让项目从“能跑”变成“能证明算法有效”。它包含 Beam Search、候选去重、多样性重排、benchmark、metrics、ablation 和 quality gate。

| 模块 | 具体怎么做 | 为什么这样做 | 可观察结果 |
| --- | --- | --- | --- |
| Beam Search | 保留多个高分候选，按深度继续扩展 | 单次生成可能错过正确补丁 | Beam Success Rate |
| Candidate Deduplication | 对候选源码和编辑做 fingerprint，过滤重复补丁 | 节省有限 sandbox budget | deduplicated candidates |
| Diversity Reranking | 鼓励不同规则、不同编辑策略的候选进入执行预算 | 避免同一种失败补丁挤占候选池 | diversity-assisted success |
| PatchScore Weight Search | 离线重排候选权重并比较 Top-1 Success、MRR、First Success Rank | 证明执行反馈、风险惩罚等权重是否有用 | 最佳权重 profile |
| Hard-case Generation | 从失败、弱 slice、fragile margin 中挖掘下一批难例 | 让 benchmark 持续暴露短板 | generated hard cases |
| Quality Gate | 检查指标、来源、覆盖、消融、provenance、showcase 是否达标 | 防止只看漂亮数字 | pass/fail checks |

![图 5：Benchmark 与评估闭环](project_report_diagrams/benchmark_loop.png)

### 6.2 为什么必须做 ablation

如果只展示最终准确率，面试官可能会问：这个结果是不是靠测试样例太简单？是不是某个模块其实没有用？

Ablation 的作用就是关闭单个模块重新评估，例如：

- without SBFL：去掉测试覆盖信号。
- without Program Graph：去掉图传播和调用依赖。
- without Static Rules：去掉 AST 规则先验。
- without Beam Search：只保留单候选修复。
- without Reflection：不允许失败后自我修复。
- without Data Dependency：去掉数据流证据。

如果关闭某个模块后 Top-1、MAP、Patch Success 或校准指标下降，就能证明这个模块不是装饰，而是有实际贡献。

## 7. Benchmark 与 GitHub raw source 链路

### 7.1 为什么 benchmark 是项目含金量来源

代码智能体项目如果没有 benchmark，很容易变成“演示看起来能跑”。benchmark 的作用是让结果可复现、可比较、可解释。

| 阶段 | 怎么做 | 为什么 | 结果 |
| --- | --- | --- | --- |
| Source 固定 | 记录 GitHub raw source、ref、sha256、license | 保证源码可复现，避免浮动分支污染评估 | sources.json |
| Template 定义 | 声明 mutation、测试文件、ground truth、expected rule | 把真实源码包装成可评估 case | templates.json |
| Materializer | 下载或读取缓存源码，注入缺陷和测试，生成独立 repo | benchmark runner 需要可执行目录 | generated manifest |
| Runner | 执行定位、补丁搜索、sandbox、metrics | 把每个 case 转成统一结果 | benchmark_report |
| Ablation | 关闭单个模块重新评估 | 证明模块贡献，不只看总分 | ablation_report |
| Showcase | 抽取关键指标和代表 case trace | 服务简历、答辩和 GitHub README | resume_showcase |

### 7.2 当前未完成的关键边界

任意 GitHub repo URL 一键自动 benchmark 化还没有完全完成，因为真实仓库通常缺少三样东西：

1. 明确失败测试。
2. 缺陷 ground truth。
3. 可验证 oracle。

当前项目更准确的能力是：

> 把 GitHub raw source 包装成可复现 mutation benchmark，并持续推进 repo onboarding 自动化。

这句话适合写进报告和面试，不会夸大项目范围。

## 8. 从 0 学习这个项目的路线

| 时间 | 学习目标 | 你应该能说清楚什么 | 建议产出 |
| --- | --- | --- | --- |
| 第 1 天 | 理解项目目标和四阶段闭环 | 为什么它不是简单 GPT 改代码 | 画出端到端流程图 |
| 第 2-3 天 | 学习 AST、函数边界、规则检测 | 为什么字符串搜索不够 | 解释一个规则如何命中 |
| 第 4-5 天 | 学习 Call Graph、CFG、Data-flow、Program Graph | 为什么 bug 可以沿调用链或数据流传播 | 画出一个小函数的图 |
| 第 6-7 天 | 学习 SBFL、GraphScore、FinalScore | 多个定位信号如何融合 | 解释 Top-k 排名 |
| 第 8-9 天 | 学习补丁生成、AST 校验、sandbox | 为什么执行验证比文本判断更可靠 | 解释一次 patch success |
| 第 10-11 天 | 学习 Beam Search、dedup、diversity reranking、reflection | 为什么搜索策略能提升修复率 | 解释一次失败到成功的闭环 |
| 第 12-14 天 | 学习 benchmark、metrics、ablation、quality gate | 如何证明模块贡献 | 准备简历 bullet 和面试回答 |

## 9. 简历应该怎么写

### 9.1 一句话项目名

> 基于异构程序图、SBFL 缺陷定位与搜索式自动修复的代码智能 Agent。

### 9.2 推荐简历 Bullet

- 构建面向 Python 仓库的代码智能 Agent，结合 AST、CFG、Call Graph、Data-flow 和 pytest trace 构建异构 Program Graph，实现函数级 Top-k 缺陷定位。
- 设计融合 SBFL、GraphScore、StaticRuleScore、SemanticSimilarity 和可选 LLMScore 的 FinalScore 排序算法，并通过 attribution、confidence calibration 和 ablation study 分析各信号贡献。
- 实现搜索式 Patch Generation：在 Top-k suspicious functions 内生成候选补丁，经过 AST/scope/risk 校验、candidate deduplication、diversity reranking 后进入 pytest sandbox 验证。
- 实现 execution-feedback reflection loop，将失败类型、stdout/stderr/traceback、历史失败补丁 fingerprint 和跨文件调用上下文反馈给下一轮补丁生成，提高修复闭环可解释性。
- 构建 GitHub raw-source mutation benchmark 与实验评估链路，输出 Top-1/Top-3、MRR、MAP、Patch Success Rate、Beam Success Rate、ablation、quality gate 和 showcase report。

### 9.3 不建议写的内容

- 不要写“支持任意 GitHub 仓库一键真实 bug 自动修复并提交 PR”，这一步还在深化。
- 不要只写“调用大模型实现自动修复”，这样会掩盖项目的算法核心。
- 不要只写最终准确率，要写清楚数据来源、benchmark 类型和 ablation 证据。
- 不要把 API key 写进简历、报告、代码或 README，只写使用环境变量配置 LLM judge。

## 10. 面试时怎么讲

### 10.1 30 秒版本

我做的是一个代码智能 Agent，不是单纯让 LLM 改代码。系统先解析 Python 仓库，构建 AST、CFG、Call Graph 和 Data-flow，再用 SBFL、GraphScore、StaticRuleScore 等信号做函数级缺陷定位。定位到 Top-k 函数后，系统生成多个补丁候选，通过 AST/scope/risk 校验和 pytest sandbox 验证。如果失败，会把执行反馈送入 reflection loop 继续修复。最后用 benchmark、ablation 和 quality gate 证明每个模块的贡献。

### 10.2 高频问答

| 问题 | 建议回答 |
| --- | --- |
| 和普通 LLM 改代码有什么区别？ | 普通 LLM 主要靠语言生成，本项目用程序图和测试反馈约束生成范围，并用 sandbox 验证结果。LLM 是一个组件，不是唯一决策者。 |
| 为什么要函数级定位？ | 直接全仓库修复搜索空间太大。先定位 Top-k 函数，可以减少误改、降低 token 成本，并让补丁生成更可控。 |
| FinalScore 怎么设计？ | 它融合测试覆盖、图结构、静态规则、语义/LLM 分数和风险惩罚。每个分量都能在报告中解释和消融。 |
| 为什么需要 Data-flow？ | 很多缺陷不是调用关系本身，而是某个值沿参数、赋值、下标访问传播后触发错误。Data-flow 能解释这种传播链。 |
| sandbox 有什么作用？ | 它把补丁质量从文本判断变成执行证据。只有 pytest 通过且无超时/运行时错误，补丁才算成功。 |
| Beam Search 解决什么？ | 单次生成容易错过正确补丁。Beam Search 保留多个候选和多轮 refined child，提高在固定执行预算内找到成功补丁的概率。 |
| 如何证明算法模块有效？ | 用 ablation study。关闭某个模块后重新跑 benchmark，如果 Top-1、MAP、Patch Success 或校准指标退化，就能证明该模块贡献。 |
| 项目目前最大未完成点？ | 任意 GitHub repo URL 到真实 bug benchmark 的一键自动化仍未完全完成，因为需要自动发现 failing tests、ground truth 和可验证 oracle。 |

## 11. 最终你应该掌握的知识地图

| 层级 | 你要理解的核心 | 可以展示的能力 |
| --- | --- | --- |
| 程序分析 | AST、CFG、Call Graph、Data-flow | 能解释代码如何被结构化建模 |
| 图推理 | Program Graph、PageRank、caller impact、module dependency、slice evidence | 能解释为什么某个函数更可疑 |
| 缺陷定位 | SBFL、GraphScore、StaticRuleScore、FinalScore、Top-k | 能解释定位排序公式和指标 |
| 自动修复 | Patch generation、AST validation、sandbox、reflection | 能解释补丁如何从失败变成成功 |
| 搜索算法 | Beam Search、candidate dedup、diversity reranking、PatchScore | 能解释如何节省执行预算并提高成功率 |
| 实验评估 | benchmark、metrics、ablation、quality gate、showcase | 能证明项目不是 demo，而是可复现实验系统 |

> 最后的学习目标：你不是要背代码，而是要能从输入、算法、执行验证和评估证据四个角度讲清楚：这个 Agent 为什么可信、哪里有效、哪里还没完成。
# 2026-07-03 项目包装更新：当前最终目标与验收状态

本项目当前应定位为：**面向公开 Python GitHub 仓库的代码智能 Agent**。用户输入 `owner/repo` 或 GitHub URL 后，系统自动完成仓库发现、源码筛选、结构建模、静态缺陷信号挖掘、函数级 Top-k 缺陷定位、测试环境诊断、可选 pytest 执行、补丁生成与沙箱验证，并通过 `AgentController` 输出 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 的可审计智能分析报告。

需要特别区分两件事：

| 能力 | 当前状态 | 面试时怎么说 |
| --- | --- | --- |
| 任意公开 Python GitHub 仓库智能分析 | 已完成到可展示、可写简历阶段 | 可以分析仓库结构、测试环境、静态/动态证据、Top-k 定位、patch validation 或 blocker |
| 任意 GitHub repo 自动构造真实 bug benchmark ground truth | 仍属于后续增强 | 不能夸大成任意仓库都能自动生成 failing tests、oracle 和真实缺陷 ground truth |

## 当前验收证据

最近一次 `repo_intelligence_agent_cli_default_output_acceptance` 使用 5 个真实 GitHub 仓库进行端到端验收，结果如下：

| 指标 | 结果 | 说明 |
| --- | ---: | --- |
| Runs | 5 | 覆盖 pypa/sampleproject、pytest-dev/pluggy、octocat/Hello-World、TheAlgorithms/Python、karpathy/nanoGPT |
| Agent Passed Runs | 5/5 | Agent 主流程符合预期 |
| Objective Compliance Pass Runs | 5/5 | 仓库拉取、结构建模、测试诊断、Top-k 定位、patch/reflection 等目标均覆盖 |
| Agent Controller Loop Complete Runs | 5/5 | 每个样例都有 Observe / Plan / Act / Verify / Reflect / Replan 证据 |
| Repository Test Patch Validation Successes | 1 | TheAlgorithms/Python gronsfeld case 验证成功 |
| Repository Test Reflection Successes | 1 | 初始补丁失败后，reflection refined patch 成功 |

## 从 0 理解这个 Agent 的最短路径

| 步骤 | 系统怎么做 | 为什么这样做 | 做完后的结果 |
| --- | --- | --- | --- |
| 1. 读入 GitHub 仓库 | 解析 URL / owner-repo / ref，读取 discovery cache 或 GitHub source tree | 明确分析对象，避免分支漂移和源码来源不确定 | 得到 repo spec、ref、source list、输出目录 |
| 2. 源码筛选 | 过滤缓存、输出目录、非 Python 文件、无关测试/文档，并可用 include/exclude 缩小范围 | 任意仓库文件很多，直接全量分析会慢且噪声大 | 得到可分析 Python source candidates |
| 3. 仓库结构建模 | 用 AST 提取函数、类、导入、调用、行号、规则命中 | 后续定位必须知道“函数在哪里、谁调用谁、风险在哪” | 输出 `repository_structure`、`repo_graph` |
| 4. 缺陷信号挖掘 | 用静态规则、调用图传播、动态图测试证据、SBFL 形成多信号 | 单一信号不稳定，多信号融合更适合真实仓库 | 得到 `StaticRuleScore`、`GraphScore`、`DynamicEvidenceScore`、`SBFLScore` |
| 5. Top-k 缺陷定位 | 将多种信号融合为 `FinalScore`，按函数级排序 | 把全仓库搜索空间压缩到少量高可疑函数 | 输出 `fault_localization` 和 Top-k suspicious functions |
| 6. 测试环境诊断 | 检测 pytest/unittest/tox/nox、依赖、推荐命令、执行风险 | 真实 GitHub 仓库经常缺依赖、无测试、或测试命令不可直接跑 | 输出 `repository_test_environment` 和 `repository_test_execution_plan` |
| 7. 可选测试执行 | 对可执行命令运行 pytest/unittest，并记录 stdout/stderr/return code | 动态证据比纯静态猜测更强，但必须可复现 | 输出 `repository_test_execution_result` 和动态证据 |
| 8. 补丁生成与验证 | 在 Top-k 函数附近生成最小 patch，通过 AST/scope/safety gate 后 sandbox 验证 | 防止大模型或规则乱改全仓库代码 | 输出 patch candidates、validation result、best patch |
| 9. Reflection | 如果初始 patch 失败，读取失败类型、旧 diff 和测试反馈生成 refined candidate | 自动修复常常第一轮不准，需要利用执行反馈改进 | 输出 `reflection_trace`，成功时产生 depth>0 patch |
| 10. Agent Controller 决策 | 每一轮都观察当前证据，选择下一步动作，验证结果并重规划 | 这让系统不是固定脚本，而是能根据 blocker 改变策略的 Agent | 输出 `github_repo_agent_controller` 决策链 |

## 三个真实示例

1. `pytest-dev/pluggy`：测试可执行，`python -m pytest -q testing` 得到 `139 passed`。Agent 没有强行生成补丁，而是将 passing tests 作为 regression guard，并要求提供 failing test / bug report 才能进入修复。
2. `octocat/Hello-World`：没有可分析 Python 源码。Agent 输出 `source_import_or_parse_missing`，选择 `adjust_source_filters`，并给出改变分析范围或提供外部证据的下一步。
3. `TheAlgorithms/Python`：gronsfeld case 中 Top-1 定位到 `gronsfeld`。初始补丁失败后，reflection 生成 depth=1 refined patch，增加 `if not key_len: return 0`，目标 pytest 验证成功。

详细示例见 `docs/examples/README.md`。

## 简历写法与面试材料

本项目已经整理出两份单独材料：

- `RESUME_AGENT_PROJECT.md`：简历短版、长版、算法向 bullet、1/3/5 分钟面试讲法。
- `INTERVIEW_QA_AGENT_PROJECT.md`：18 个常见面试问题与可直接回答的答案。
- `docs/showcase/README.md`：GitHub 展示入口，集中放置架构图、报告样例、真实仓库示例和有限鲁棒性矩阵。

简历中建议使用“面向公开 Python GitHub 仓库的智能分析 Agent”这种准确表述，不建议写“任意仓库 100% 自动修复”。项目亮点应突出 AST / Call Graph / Program Graph / SBFL / GraphScore / FinalScore / Patch Validation / Reflection，以及 AgentController 的可审计决策闭环。
