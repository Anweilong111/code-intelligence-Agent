# Code Intelligence Agent V3 中文简历与面试材料

## 1. 使用原则

本材料只引用当前仓库中可审计的 V3 证据。当前离线发布门通过，但 60 次 LLM
和 60 次 Hybrid 真实模型 trial 尚未执行，因此简历中不能写“真实 LLM 修复率”
或“自动修复成功率”。完成 live 评估后，必须从统一发布 artifact 读取数字，
不能手工估算。

权威状态：
[`docs/v3/phase7_unified_evaluation.json`](../v3/phase7_unified_evaluation.json)。

## 2. 项目名称与一句话介绍

### 推荐项目名称

```text
Code Intelligence Agent V3：面向真实 Python 缺陷的代码定位与受控自动修复 Agent
```

### 一句话版本

```text
构建面向真实 Python GitHub 缺陷的代码智能 Agent，融合 AST/Program Graph、
真实 coverage/traceback、多证据函数级 Top-k 定位、Rule/LLM/Hybrid 补丁、
sandbox 与语义验证、结构化记忆和安全控制，形成可审计的
Observe -> Plan -> Act -> Verify -> Reflect -> Replan 闭环。
```

### 30 秒版本

```text
我做的是一个面向真实 Python GitHub 仓库的 Debugging/Repair Agent。系统先
诊断仓库结构和测试环境，再融合 AST、程序图、真实失败测试、coverage、
traceback、语义、复杂度和变更历史做函数级 Top-k 定位。LLM 可以参与规划、
语义补丁和失败反思，但只能输出结构化 proposal；Action Registry 和 Safety
Gate 决定能否执行，targeted tests、完整回归和语义 oracle 决定是否算修复。
我还构建了 20 个真实 bug benchmark、repository-disjoint 定位实验、记忆与
恶意仓库安全评估，以及拒绝不完整或模型/Prompt 漂移结果的统一发布审计器。
```

## 3. 当前可直接写入简历的版本

### 3.1 三条标准版

```text
- 设计并实现面向真实 Python GitHub 缺陷的代码智能 Agent，通过 AgentController
  编排 Observe -> Plan -> Act -> Verify -> Reflect -> Replan；LLM 仅生成结构化
  规划/补丁/反思建议，所有动作受 Action Registry、Schema、风险和预算门控，
  修复成功由 targeted pytest、完整回归和语义验证共同裁决。

- 构建函数级多证据缺陷定位算法，融合真实 Ochiai SBFL、失败测试、traceback、
  词法语义、复杂度和 Git history；在 validation split 搜索 141 组权重后冻结，
  在 5 个 held-out 真实 bug 上取得 Top-1/3/5=0.60/0.80/1.00，移除动态证据后
  Top-1 下降 0.40，并为每个函数保留逐信号贡献和分数重建证据。

- 建立 20 个可复现真实 bug、6 个仓库的 BugsInPy benchmark，要求 bug 测试失败、
  fix 目标测试与完整回归通过且 gold patch 对模型不可见；将隔离测试启动终止率
  做到 19/20，并实现 Rule/LLM/Hybrid trial 归因、语义正确性门、结构化记忆、
  8 类恶意仓库防护和 120-trial 完整性/模型漂移发布审计。
```

### 3.2 偏算法岗位版

```text
- 设计真实缺陷函数定位实验：按仓库划分 development/validation/test，validation
  侧搜索 141 个 simplex profile，以 MAP、MRR、nDCG@3、Top-k 和 EXAM 的组合
  目标选出冻结权重，禁止 test ground truth 参与调参。

- 将 FinalScore 拆分为可审计 contribution；冻结 profile 融合 SBFL 0.225、
  Semantic 0.250、TestFailure 0.175、Traceback 0.100、Complexity 0.125 和
  ChangeHistory 0.125，在 5-case test split 上取得 MRR=0.7067、MAP=0.6144、
  nDCG@3=0.6226，并通过 without-dynamic/semantic/auxiliary 消融分析信号贡献。

- 构建真实修复评估契约，按 case-strategy-trial 固定独立性，严格区分 Rule、LLM、
  Hybrid generator attribution；保留 provider/environment/application 失败分母，
  使用 Wilson 95% 区间呈现小样本不确定性，拒绝 119/120 的伪完整结果。
```

### 3.3 偏大模型 Agent 岗位版

```text
- 实现 LLM Planner + Rule Safety Controller：模型输出 selected_action、arguments、
  evidence、risk、fallback 和 termination condition；控制器独立校验动作注册、
  参数 allowlist、状态迁移、确认策略与动作/时间/成本预算，provider 或 Schema
  失败时回退规则规划器。

- 实现 bounded-context LLM patch/reflection：只注入 Top-k 函数、必要图邻域、
  failing test、traceback、用户约束和失败 fingerprint，隔离 gold patch 与
  ground truth；模型无写盘和 Shell 权限，候选统一进入 AST/scope/safety/pytest
  和语义验证链。

- 构建 Working/Session/Repo/Repair/Cross-repo 五层结构化证据记忆，按 repo/ref、
  provenance、有效期和 authority 检索；受控实验 completion 从 3/7 提升到 7/7，
  过期复用、冲突执行和 advisory 越权均为 0，未在无消融收益时盲目接入向量库。
```

### 3.4 偏工程与安全岗位版

```text
- 实现 Python 仓库布局、pytest/unittest/tox/nox、Poetry/uv、monorepo 工作目录、
  Python 版本和依赖 blocker 诊断，在固定 20 仓库上实现 20/20 测试命令发现、
  19/20 测试进程真实启动并终止，其余案例输出准确 environment blocker。

- 建立进程级不可信仓库防护，覆盖 prompt injection、恶意 build hook、路径穿越、
  symlink、敏感变量、Python 外部 socket 和资源耗尽；8/8 受控安全案例被拒绝、
  隔离或准确报告，同时明确 native child 与硬资源配额仍需要容器/Job Object。

- 设计统一 RunRecord 和发布审计器，记录 provider、精确 model ID、Prompt hash、
  token、成本、延迟、候选 provenance、测试和失败 taxonomy；完整回归
  1381 passed、2 个 Windows symlink fixture skip，release hygiene 5/5。
```

### 3.5 一行压缩版

```text
开发真实 Python 缺陷驱动的代码智能 Agent，融合可解释 Top-k 定位、
Rule/LLM/Hybrid 补丁、pytest+语义验证、结构化记忆与安全控制，并在 20 个
固定 bug、19/20 仓库启动和 repository-disjoint 消融协议上完成可审计评估。
```

## 4. 当前允许写的量化结果

| 指标 | 当前结果 | 简历解释 |
| --- | ---: | --- |
| Benchmark | 20 accepted / 5 rejected / 6 repos | 真实 BugsInPy 固定 SHA 缺陷 |
| 测试命令发现 | 20/20 | 不等于测试通过或修复成功 |
| 测试进程启动并终止 | 19/20 | 独立环境；剩余 1 个有 blocker |
| 定位 Top-1/3/5 | 0.60/0.80/1.00 | 5 个冻结 test case |
| MRR / MAP / nDCG@3 | 0.7067/0.6144/0.6226 | 函数级真实 bug 定位 |
| without-dynamic Top-1 差值 | 0.40 | 当前 test split 上的正贡献 |
| without-semantic Top-1 差值 | 0.20 | Semantic 为确定性词法信号 |
| Rule pass@1 | 0/20 | 正式真实基线，不能省略 |
| LLM/Hybrid pass@k | pending | 不得写入简历数字 |
| 语义校准 | 2/2 human fixes | 不是 Agent repair |
| 记忆 completion | 3/7 -> 7/7 | 受控记忆消融 |
| 安全处理 | 8/8 | 受控 hostile-repo fixture |
| 全量回归 | 1381 passed, 2 skipped | 离线 Phase 7 证据 |

### 正确表达

```text
在 20 个真实 Python bug 上建立可复现 oracle 和 Rule/LLM/Hybrid 独立 trial
协议；当前 Rule pass@1 为 0/20，真实 LLM/Hybrid 评估仍待完成，因此未把离线
fixture 或人工 fix 写成模型修复率。
```

### 禁止表达

```text
支持任意 GitHub 仓库并实现 100% 自动修复。
真实大模型修复率达到 XX%。
Graph 让定位准确率提升 XX%。
Agent 成功修复了 2 个真实案例。
```

禁止原因：live 指标尚未测量；被选定位 profile 的 Graph 权重为 0；Phase 5
的 2/2 是人工 fix 校准。

## 5. 完成 live 评估后的更新模板

只有
[`phase7_unified_evaluation.json`](../v3/phase7_unified_evaluation.json)
变为 `status=pass` 且 `claim_eligible=true` 后，才把以下占位符替换为真实值：

```text
- 在 20 个真实 bug、每案例 3 次独立 trial 上评估 LLM/Hybrid：LLM pass@1=<A>、
  pass@3=<B>，Hybrid pass@1=<C>、pass@3=<D>；verified repair=<E>/<F>，
  Reflection recovery=<G>/<H>，总成本 <$I>，P50/P95 延迟=<J>/<K>，并保留
  provider、environment 与 application failure 分母。
```

不要从单案例 smoke、部分 trial 或 provider retry 推导上述数字。

## 6. 两分钟项目讲解

```text
这个项目解决的是：给定一个真实失败的 Python 仓库，Agent 如何基于可验证
证据定位根因并尝试最小修复，而不是只让大模型阅读代码后猜答案。

第一层是仓库与环境。我识别包布局、源码根、runner、Python 和依赖约束，在
隔离环境运行真实 failing tests，并把不能启动的情况分类为 environment blocker。

第二层是算法定位。我抽取 AST、调用和程序结构，再融合 Ochiai coverage、
失败测试、traceback、语义、复杂度和 Git history。权重不是在 test 上手调，
而是在 validation 上搜索 141 个 profile 后冻结。每个 Top-k 结果保存 raw score、
weight 和 contribution，所以可以解释为什么某个函数排第一。

第三层是 Agent。LLM 可以提出下一步动作、生成语义补丁和做失败反思，但只能
输出结构化 proposal。AgentController 根据当前 observation、memory 和预算做
最终动作选择，Action Registry 决定权限。工具结果会改变下一轮状态，因此不是
固定顺序流水线。

第四层是验证。Rule、LLM、Hybrid 候选都要经过 AST、修改范围、公共签名、
测试保护和危险 API 检查，再运行 targeted tests、完整回归和适用语义 oracle。
LLM Judge 只排序，pytest 和语义门决定 verified repair。

最后我用 20 个真实 bug、固定 SHA、独立 trial 和统一 RunRecord 做评估。当前
离线定位、环境、Rule、语义、记忆和安全证据已经完成，真实 LLM/Hybrid 120 次
试验仍 pending，所以项目报告明确保留了这个边界。
```

## 7. FinalScore 深挖答法

### Q1：最终分数怎么计算？

V3 冻结 profile 为：

```text
FinalScore = clamp(
    0.225 * SBFL
  + 0.250 * Semantic
  + 0.175 * TestFailure
  + 0.100 * StackTrace
  + 0.125 * Complexity
  + 0.125 * ChangeHistory
)
```

Graph、Static、LLM 和 Risk 在这个 profile 中为 0。不是先假设公式，再汇报最好
结果；系统在 validation 上搜索 141 个候选，以 MAP、MRR、nDCG@3、Top-k 和
EXAM 的组合目标选择，然后冻结到 test。

### Q2：为什么 Graph 权重为 0，还保留 Program Graph？

Graph 是实现完整的候选信号和解释结构，但当前 validation 认为它没有提高稳健
目标。诚实做法是保留模块和消融结果，不把无收益写成收益。原因可能是样本小、
动态证据强或当前图特征粒度不足；后续应扩充跨函数/数据流 test，而不是在 test
上重新调权。

### Q3：一个函数的分数如何重建？

假设函数的 SBFL=0.8、Semantic=0.6、TestFailure=1、StackTrace=0.5、
Complexity=0.4、History=0.2：

```text
0.225*0.8 + 0.250*0.6 + 0.175*1.0 + 0.100*0.5
+ 0.125*0.4 + 0.125*0.2 = 0.63
```

artifact 保存每一项 contribution 和 reconstruction check，避免只输出黑盒总分。

### Q4：Top-5=1.0 是否说明定位已经解决？

不是。test 只有 5 个案例且来自一个仓库；Top-5 5/5 的 Wilson 95% 区间约为
`[0.5655, 1.0000]`。它说明在当前冻结集上都进入 Top-5，不代表任意仓库 100%。

## 8. 高频 Agent 面试问题

### Q1：这为什么是 Agent，不是固定工作流？

底层工具是确定模块，但控制器每轮根据仓库状态、测试证据、blocker、memory、
动作历史和剩余预算重新选择动作。安全提议可能执行、拒绝、等待确认或 fallback；
执行结果又改变下一轮 observation。系统有目标、观察、动作选择、真实执行、验证、
反思、重规划、终止条件和跨轮记忆，因此是受控 Agent，而不是固定顺序脚本。

### Q2：为什么不让 LLM 完全负责规划？

规划能力和执行授权必须分离。模型可能产生不存在的动作、危险参数、重复动作或
错误风险判断。LLM 负责语义候选，规则控制器负责不可妥协的权限、预算和状态
不变量。这种 hybrid control 比“全部规则”更灵活，也比“模型自由执行 Shell”
更可验证。

### Q3：LLM 具体用在哪？

自然语言 intent、Planner/Replanner、规则覆盖不到的语义 patch、失败后的
Reflection，以及可选 Judge 排序。AST、图构建、coverage、pytest、Scope Gate、
Safety Gate 和成功裁决不依赖模型。

### Q4：当前不是还没跑真实 LLM 吗？

基础设施和协议已经接入真实 provider，但 V3 冻结 benchmark 的 120 次正式
LLM/Hybrid trial 还没有 fresh key 证据，因此我不宣称 live 修复率。项目已经
完成的是真实 benchmark、模型调用/记录/归因链、Rule 基线和严格发布门；下一步
是付费执行，不是重新实现一套假 fixture。

### Q5：Reflection 与普通重试有什么区别？

provider retry 只解决网络、限流等传输问题，仍属于同一个 trial。Reflection
发生在候选通过生成与安全门、但测试或语义验证失败之后；它读取新的 nodeid、
异常、traceback 和约束，关联父候选 fingerprint，并生成实质不同的新候选。

### Q6：什么情况下停止？

验证成功、终止 blocker、证据不足、没有新候选、重复状态、Reflection 轮数耗尽、
动作/时间/成本预算耗尽或需要人工确认时停止。停止也必须生成原因和下一步建议。

## 9. 补丁生成与验证面试问题

### Q1：Rule/LLM/Hybrid 分别怎么做？

- Rule：finding 到确定模板，可复现、零模型成本，但覆盖窄。
- LLM：bounded context 到结构化候选，覆盖语义缺陷，但不稳定且有成本。
- Hybrid：合并两类候选并保留 generator provenance，不把 Rule 成功归因给模型。

三种策略进入同一安全和测试链，保证对比的成功口径一致。

### Q2：怎样防止模型看到答案？

bug/fix checkout 分离；模型运行只使用 bug SHA；模型上下文审计禁止 gold patch、
fix commit、ground-truth 选择和本地绝对路径。ground truth 只供离线评估使用，
不参与候选选择。

### Q3：怎样防止测试过拟合？

先跑 targeted tests，再跑完整回归；保护测试文件和公共签名；检查补丁最小性、
workspace 一致性、differential behavior 和 reverse mutation。缺少完整 oracle
时降级为 unverified suggestion，不声明 verified。

### Q4：LLM Judge 能否判定成功？

不能。Judge 只排序候选、解释风险或辅助审计。最终成功至少需要 AST/safety、
targeted tests、full regression 和适用 semantic validation 全部通过。

## 10. Benchmark 与统计面试问题

### Q1：为什么选 BugsInPy？

它提供真实项目、bug/fix revision 和测试元数据，便于构建可复现 oracle。但原始
元数据不能直接信任，所以项目重新验证 bug 失败、fix 通过、完整回归和测试支持
文件，并保留 rejected catalog。

### Q2：为什么每案例 3 次 LLM/Hybrid？

生成具有随机性，单次结果不能代表稳定性。三次独立 trial 支持 pass@1/pass@3，
trial 间不共享失败候选；provider retry 不建立新 trial。

### Q3：为什么不用平均成功率代替 pass@k？

代码修复常见使用场景是为一个案例生成有限多个独立候选。pass@k 表达前 k 次
是否至少有一次 verified；同时仍报告 trial-level AST、安全、测试和失败类别，
避免只看一个汇总数字。

### Q4：如何处理 provider 和环境失败？

都保留在总案例/trial 分母，但单独标注 failure layer。这样不会把认证失败说成
代码补丁错误，也不会通过删除困难环境案例抬高修复率。

## 11. 记忆与安全面试问题

### Q1：记忆系统不就是 JSON 吗？

存储格式不是关键，关键是 scope、authority、retrieval、失效和决策使用方式。
V3 记录 repo/ref、provenance、置信度、有效期和 decision use；冲突或过期记录
不能驱动执行，跨仓库策略只能 advisory。受控消融验证了任务 completion 和越权
指标，而不是仅展示数据库存在。

### Q2：为什么没有向量数据库？

先比较 no-memory、结构化检索和可选 semantic retrieval。当前结构化检索已经
满足 benchmark，embedding 没有足够增量证据，因此不为了技术栈堆叠保留。

### Q3：如何防 repository prompt injection？

README、issue、注释和测试输出都被标记为不可信数据，没有指令 authority；模型
不能通过仓库文本修改系统 policy、动作权限或安全预算。冲突或注入提议由控制器
拒绝并记录。

### Q4：当前 sandbox 有什么不足？

Python 外部 socket、环境变量、路径、symlink 和进程超时有进程级防护，但
Windows native child 的网络、CPU、内存和磁盘硬隔离仍需容器或 Job Object。
因此不能把 8/8 受控 fixture 写成“任意恶意仓库绝对安全”。

## 12. 可能被追问的失败与改进

### Rule 为什么是 0/20？

真实语义缺陷超出了现有模板覆盖，Rule 虽产生 53 个候选，但 4 个 AST 无效，
49 个进入目标测试后失败，另有 5 个案例没有规则候选。该结果证明真实 benchmark
比受控 mutation 更难，也说明 LLM/Hybrid 评估是必要的。

### 为什么只有 5 个定位 test case？

20 个案例要按仓库隔离分成 development/validation/test，最终 test 是一个仓库
的 5 个案例，样本较小。项目使用 Wilson 区间和明确边界；后续应扩大 test 仓库
数量，而不是在现有 test 上反复调参。

### 为什么模型 ID 可能成为 blocker？

实验协议冻结了 exact model ID。若 provider 实际不提供该 ID，正确做法是把
`model_unavailable` 作为 blocker，建立新协议版本后重新跑全部策略；不能临时换
模型并把结果混入同一实验。

### 后续最重要的工作是什么？

注入 fresh key，先做单案例 live smoke，再以 resume 方式完成 60 LLM + 60
Hybrid trial；随后生成真实 pass@1/pass@3、verified repair、Reflection、成本、
延迟和失败报告。第二优先级是扩充多仓库定位 test 和容器级恶意仓库隔离。

## 13. 面试材料打开顺序

1. `README.MD`：项目入口和当前状态；
2. `docs/v3/v3_architecture_and_agent_design_cn.md`：架构与算法；
3. `docs/v3/phase4_localization_metrics.json`：FinalScore 和消融；
4. `docs/v3/phase7_unified_evaluation.json`：所有指标与 pending 边界；
5. `docs/v3/v3_ten_minute_demo_guide_cn.md`：现场演示脚本。

面试前逐条确认简历数字仍与统一报告一致。完成 live 试验后，应更新本材料，
不能只修改简历而不提交新的 source artifact 和发布审计。
