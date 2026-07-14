# Code Intelligence Agent 系统学习与面试准备指南

> **V2 阅读提示（2026-07）**：本文保留从基础概念到实现链路的完整学习内容，其中部分实验数字和命名来自 V1。面试前请先阅读最新的 [V2 架构与算法设计](../v2/architecture_and_design.md)、[Phase 8 案例集](../v2/phase8_case_studies.md) 和 [V2 中文简历与面试材料](v2_resume_interview_pack_cn.md)。涉及权重、陌生仓库统计、Planner/Memory/Patch 消融和能力边界时，以这些 V2 文档及 Phase 7 artifact 为准。

这份文档的目标不是让你从源码开始硬啃项目，而是让你先建立一套完整的“运行视角”：用户输入一个 GitHub 仓库后，Agent 每一步到底做了什么、怎么做、为什么这么做、做完后会得到什么结果，以及面试时应该如何讲清楚。

你可以把这个项目理解成一个面向 Python GitHub 仓库的代码智能分析与修复 Agent。它不是简单把仓库内容交给大模型总结，而是先做仓库理解、结构建模、缺陷信号挖掘、测试环境诊断，再由 Agent Controller 按 Observe -> Plan -> Act -> Verify -> Reflect -> Replan 的闭环选择下一步动作。大模型主要用于规划增强、补丁生成、失败反思和候选评判，但所有动作都要经过 action registry、安全门控和 sandbox 验证。

## 1. 先明确最终目标

### 1.1 这个项目最终想做到什么

最终目标可以这样描述：

```text
构建一个面向任意 Python GitHub 仓库的代码智能 Agent。
用户输入 GitHub repo 后，系统自动完成仓库拉取、源码筛选、结构建模、静态缺陷信号挖掘、函数级 Top-k 缺陷定位、测试环境诊断、可选 pytest/unittest/tox/nox 执行、补丁生成与沙箱验证。
Agent Controller 会根据当前 blocker 自动规划下一步动作，并在失败后反思、重规划，最终输出可审计的智能分析报告。
```

这里的关键词有四个：

- **任意 Python GitHub 仓库**：不是只支持一个固定 demo，而是要能处理不同仓库结构、不同测试方式、不同依赖状态。
- **智能分析**：不是只输出目录树，而是要理解代码结构、调用关系、风险信号、测试状态和下一步动作。
- **Agent 闭环**：不是固定工作流，而是根据观察到的状态决定继续测试、调整范围、生成补丁、验证补丁或报告 blocker。
- **可审计**：每一步为什么这么做、用了什么证据、失败在哪里、下一步是什么，都要能在报告里看见。

### 1.2 不能夸大的地方

这个项目可以写成算法向 Agent 项目，但不要把它夸成“任意 GitHub 仓库都能 100% 自动修复真实 bug”。更准确的说法是：

- 它可以对大量公开 Python 仓库完成结构化智能分析。
- 它可以在有 failing evidence 或可构造受控测试时进入补丁生成与验证。
- 它可以在证据不足、无测试命令、依赖不可复现、非 Python 仓库等情况下输出 blocker 和下一步建议。
- 它不会在没有可验证 bug 的情况下强行编造修复结果。

面试时这点非常重要。真实工程系统的价值不只是“成功修复”，还包括“知道什么时候不能修、为什么不能修、下一步需要什么证据”。

## 2. 总体理解：它为什么是 Agent，而不是普通脚本

### 2.1 普通工作流是什么样

普通工作流通常是固定顺序：

```text
拉取仓库 -> 扫描代码 -> 运行测试 -> 输出报告
```

无论仓库有没有测试、有没有 Python 源码、测试是否失败、LLM 是否可用，它都按固定顺序往下跑。这种系统更像自动化脚本或 pipeline。

### 2.2 当前项目的 Agent 特征

当前项目的 Agent 特征主要体现在：

- **有观察**：先判断仓库结构、源码可用性、测试命令、动态证据、LLM 可用性、历史失败补丁。
- **有规划**：根据当前状态选择下一步动作，例如运行测试、扩大源码范围、进入补丁验证、输出 blocker。
- **有动作**：动作不是随便执行，而是来自 action registry 中的受控 action。
- **有验证**：测试执行、patch validation、artifact 检查都会产生验证结果。
- **有反思**：失败后分析失败类型、失败输出、旧 patch 指纹和 blocker。
- **有重规划**：根据反思结果继续生成 refined patch、请求外部输入或停止。
- **有记忆**：保存 session、用户约束、失败 patch、repair strategy 和历史分析结果。
- **有多轮延续**：可以围绕同一个 repo session 继续解释失败、修改约束、重跑测试或继续修复。

{{DIAGRAM:agent_pipeline|从 GitHub 仓库到智能分析报告的主链路}}

### 2.3 用一句话讲给面试官

你可以这样讲：

> 我做的是一个面向 Python GitHub 仓库的代码智能 Agent。它不是把仓库直接塞给大模型，而是先做 repo discovery、AST/Call Graph/Program Graph 建模、静态缺陷信号挖掘和测试环境诊断，再由 AgentController 根据当前证据进行 Observe -> Plan -> Act -> Verify -> Reflect -> Replan。LLM 参与规划、补丁生成和失败反思，但所有动作都经过白名单、安全门控和 sandbox 验证，最终输出可审计报告。

## 3. 你应该如何学习这份文档

### 3.1 不要从源码开始

如果你是初学者，不建议第一步就打开源码文件逐行读。更合适的学习顺序是：

1. 先理解用户输入一个仓库后系统会输出哪些报告。
2. 再理解每份报告背后代表的 Agent 阶段。
3. 再理解每个阶段的算法思想。
4. 最后再回到源码验证实现细节。

原因是这个项目模块很多，如果从代码入口开始读，很容易迷失在参数、数据类、测试夹具和报告格式里。先建立“运行链路”会更清楚。

### 3.2 建议先看四类输出

运行一次 Agent 后，优先看这些输出：

- `github_repo_intelligence.md`：最终智能分析报告，适合了解整体结果。
- `agent_decision_report.md`：Agent 每一步为什么这么决策。
- `agent_execution_trace.md`：每个 action 是否真的执行、是否被阻塞、验证结果是什么。
- `agent_memory_report.md`：session、历史失败、用户约束、repair strategy 等记忆内容。

你不需要立刻看源码。先能解释这些报告里的信息，就已经能在面试中讲清楚 60% 以上。

## 4. 一个完整运行故事：以 pypa/sampleproject 为例

### 4.1 用户输入

用户输入：

```text
pypa/sampleproject
```

或者输入：

```text
https://github.com/pypa/sampleproject
```

Agent 的任务不是简单下载这个仓库，而是要回答：

- 这是一个 Python 仓库吗？
- 源码在哪里？
- 测试在哪里？
- 用 pytest、unittest、tox 还是 nox？
- 当前有没有 failing test？
- 如果没有 failing test，能否只做静态定位？
- 是否应该生成 patch？
- 如果不能生成 patch，应该输出什么 blocker？

### 4.2 Agent 的实际处理过程

以 `pypa/sampleproject` 为例，一个合理的运行过程是：

1. 识别输入是 `owner/repo` 格式。
2. 拉取或导入 GitHub 源码元信息。
3. 分析仓库结构，识别 `pyproject.toml`、源码目录、测试目录和 runner 信号。
4. 对 Python 文件做 AST 分析，提取函数、类、调用、复杂度和静态风险信号。
5. 构建 Call Graph 和 Program Graph。
6. 计算函数级可疑度 Top-k。
7. 诊断测试命令，发现可能的 unittest/nox/pytest 路径。
8. 如果测试只是通过，没有可用 failing evidence，Agent 不会强行修复。
9. AgentController 选择下一步，例如要求提供 failing test、bug report，或扩展 failure overlay。
10. 最终报告明确说明当前状态、证据、阻塞原因和下一步建议。

### 4.3 做完后的结果是什么

对 `pypa/sampleproject` 这类示例，常见结果不是“自动修复成功”，而是：

```text
仓库可分析。
源码结构可识别。
测试命令可诊断。
静态定位可以给出 Top-k 候选。
但当前没有可用 failing evidence，因此不能安全生成修复补丁。
Agent 输出 blocker：需要 failing test、bug report 或更明确的 failure overlay。
```

这其实是合理结果。因为如果测试本来是通过的，Agent 不能为了展示自动修复而虚构 bug。

### 4.4 面试怎么讲这个例子

你可以这样讲：

> `pypa/sampleproject` 这个例子体现的是 Agent 的保守决策能力。系统能完成仓库发现、源码筛选、图建模、测试诊断和 Top-k 定位，但由于没有可用 failing evidence，Agent 不会直接生成 patch，而是把 passing tests 作为 regression guard，并报告需要外部 bug 证据。这说明系统不是单纯跑固定流程，而是会根据证据决定是否进入修复阶段。

## 5. 第一步：输入仓库与仓库发现

### 5.1 做了什么

第一步是把用户输入统一成系统可处理的仓库对象。用户可能输入三种形式：

- `pypa/sampleproject`
- `https://github.com/pypa/sampleproject`
- 本地路径，例如 `D:\projects\some_repo`

系统需要判断输入类型，提取 owner、repo、ref、默认分支、目标路径和输出目录。

### 5.2 怎么做

实现思路不是让 LLM 猜，而是用确定性解析：

- 如果输入包含 `github.com`，按 GitHub URL 解析。
- 如果输入是 `owner/repo`，按 GitHub 仓库名解析。
- 如果输入是本地目录，走本地 repo parser。
- 如果用户指定 ref 或 include/exclude，则保存到 run config。
- 如果没有指定输出目录，则生成默认输出目录。

这样做的好处是可重复、可测试、可审计。相同输入会得到相同的仓库标识。

### 5.3 为什么这么做

LLM 对仓库地址的理解可能出错，例如把分支、路径、owner、repo 混淆。如果第一步就不稳定，后面的 AST、测试发现和报告都会出错。

确定性输入解析解决的是“任务边界”问题：系统必须先知道自己要分析哪个仓库、哪个版本、哪些文件。

### 5.4 做完后得到什么

这一阶段通常得到：

- 标准化 repo spec。
- 仓库来源类型：GitHub URL、owner/repo、本地路径。
- 输出目录。
- include/exclude 过滤条件。
- 初始 run metadata。

例子：

```text
用户输入：pypa/sampleproject
标准化结果：
  owner = pypa
  repo = sampleproject
  source = github
  output_dir = outputs_smoke/sampleproject_agent_run
```

### 5.5 面试怎么讲

> 我没有直接让 LLM 读取用户输入，而是先做确定性的 repo spec 解析，把 GitHub URL、owner/repo 和本地路径统一成内部表示。这样后续仓库发现、源码筛选和报告生成都有稳定输入，也方便测试不同输入格式。

## 6. 第二步：源码拉取、发现与筛选

### 6.1 做了什么

Agent 会获取仓库文件列表，并区分不同文件角色：

- 应用源码。
- 测试代码。
- 配置文件。
- 包管理文件。
- CI 或 runner 文件。
- 文档和无关文件。

### 6.2 怎么做

常见判断信号包括：

- 路径是否在 `src/`、包名目录、根目录模块下。
- 路径是否在 `tests/`、`testing/`、`test_*.py`、`*_test.py` 下。
- 是否存在 `pyproject.toml`、`setup.py`、`setup.cfg`。
- 是否存在 `tox.ini`、`noxfile.py`、`pytest.ini`。
- 文件是否为 `.py`。
- include/exclude 是否指定了分析范围。

系统会把这些信息整理成 repository profile，而不是把所有 `.py` 文件一视同仁。

### 6.3 为什么这么做

真实 GitHub 仓库的结构差异非常大：

- `pytest-dev/pluggy` 是典型 `src/` layout。
- `pypa/sampleproject` 是教学式 Python 包项目。
- `TheAlgorithms/Python` 是大量算法文件的集合。
- `octocat/Hello-World` 基本没有可分析 Python 源码。
- `karpathy/nanoGPT` 可能有 Python 文件但测试命令不一定清晰。

如果不做源码筛选，Top-k 定位可能会把测试 helper、配置脚本或示例文件误认为核心应用源码。

### 6.4 做完后得到什么

输出通常包含：

- `repository_profile`：仓库类型、源码布局、测试布局、配置文件。
- `candidate_sources`：可分析 Python 文件候选。
- `source_import`：导入或解析是否成功。
- `analysis_readiness`：是否具备继续分析条件。

例子：

```text
pytest-dev/pluggy：
  识别 src layout。
  应用源码主要在 src/pluggy/。
  测试目录可发现。
  pytest runner 信号明确。

octocat/Hello-World：
  未发现可用 Python 源码。
  Agent 输出 source_import_or_parse_missing。
  下一步建议调整分析范围或更换目标仓库。
```

### 6.5 面试怎么讲

> 源码筛选的核心是给文件打角色标签。真实仓库里应用源码、测试代码、配置代码混在一起，如果不区分 source role，后续缺陷定位可能会把测试工具或配置脚本排到 Top-1。我的系统先建立 repository profile，再进入静态分析。

## 7. 第三步：Repo Understanding

### 7.1 做了什么

Repo Understanding 是对仓库进行“工程画像”。它不只是列文件，而是回答：

- 这是库项目、应用项目、算法集合还是非 Python 仓库？
- 是否有标准包结构？
- 是否能找到测试入口？
- 是否有依赖安装信息？
- 是否需要限制 source scope？
- 是否存在明显 blocker？

### 7.2 怎么做

系统会综合多个信号：

- 文件树。
- Python 文件数量。
- packaging 配置。
- 测试目录和测试文件。
- runner 配置。
- include/exclude。
- 源码解析成功率。
- source limit 和大仓库限制。

它会把这些信号转成可读的 readiness 判断，例如：

```text
repo graph ready = true
program graph available = true
test command discovered = true
dynamic evidence usable = false
blocker = dynamic_evidence_not_usable
```

### 7.3 为什么这么做

如果没有 Repo Understanding，Agent 就不知道自己处于哪个阶段：

- 如果没有 Python 源码，应该停止并报告 source blocker。
- 如果有源码但没有测试，应该做静态分析并报告 no-test-command blocker。
- 如果测试可执行但全部通过，应该把它作为 regression guard。
- 如果有 failing test，才适合进入自动修复。

这一步让 Agent 具备“根据上下文决策”的基础。

### 7.4 做完后得到什么

输出是一个“仓库状态快照”。它会告诉后续 Controller：

- 是否继续做 AST。
- 是否构建图。
- 是否发现测试。
- 是否运行测试。
- 是否允许进入 patch generation。
- 当前 blocker 是什么。

### 7.5 面试怎么讲

> Repo Understanding 是 Agent 的观察层。它把仓库从一堆文件变成结构化状态，让 Controller 能判断当前应该进入静态定位、测试诊断、补丁验证还是 blocker 报告。

## 8. 第四步：AST 分析

### 8.1 做了什么

AST 分析会把 Python 源码解析成语法结构，并提取函数级信息：

- 函数名。
- 类名。
- 参数。
- return 语句。
- import。
- 调用表达式。
- if/for/while/try 等控制结构。
- 行号范围。
- 复杂度。
- 静态 bug pattern。

### 8.2 怎么做

系统使用 Python AST 思路，而不是字符串搜索。它会遍历语法树，找到函数定义、类定义、调用节点和控制流节点，再把这些信息归并到函数级记录中。

例如有这样一个函数：

```text
函数 mean(values)
  做法：返回 sum(values) / len(values)
```

AST 层能识别：

- 函数名是 `mean`。
- 参数是 `values`。
- 里面调用了 `sum` 和 `len`。
- 返回表达式存在除法。
- 当 `values` 为空时，可能有除零风险。

### 8.3 为什么这么做

字符串搜索只能看到文本，AST 能看到结构。比如搜索 `len(` 只能知道出现了 `len`，但不知道它是否在函数内、是否参与除法、是否被条件保护。

对代码智能 Agent 来说，AST 是所有后续分析的基础：

- Call Graph 依赖函数调用信息。
- Program Graph 依赖函数、文件、调用和风险节点。
- 静态缺陷信号依赖语法结构。
- Top-k 定位依赖函数级 feature。
- LLM patch context 也需要知道目标函数边界。

### 8.4 做完后得到什么

每个函数会得到类似这样的结构化画像：

```text
函数：mean
文件：statistics_utils.py
行号：10-12
调用：sum, len
复杂度：1
静态信号：possible_zero_division
source_role：application
```

这不是最终结论，而是后续定位的输入。

### 8.5 面试怎么讲

> AST 分析解决的是“代码结构是什么”的问题。我用它提取函数级特征，包括调用、复杂度、分支、异常处理和潜在风险信号。这样 Top-k 定位不是基于纯文本，而是基于结构化程序信息。

## 9. 第五步：Call Graph 构建

### 9.1 做了什么

Call Graph 表示函数之间的调用关系：

```text
parse_config -> load_file
parse_config -> validate_schema
run_agent -> parse_config
run_agent -> execute_action
```

它回答的是：“哪个函数调用了哪个函数？”

### 9.2 怎么做

系统从 AST 中提取调用表达式，然后建立边：

- caller：当前函数。
- callee：被调用函数名或方法名。
- file：所在文件。
- line：调用位置。
- confidence：调用解析置信度。

Python 是动态语言，静态 Call Graph 不可能 100% 精确，所以系统会保持保守：能确定的调用就连边，不确定的调用不强行推断。

### 9.3 为什么这么做

bug 的影响通常沿调用链传播。失败测试可能报错在上层函数，但真正的问题在下层工具函数；也可能错误发生在被多个函数复用的公共函数里。

Call Graph 的作用包括：

- 找到 caller/callee。
- 让 Top-k 分数考虑邻近函数。
- 帮 LLM 修复时提供上下文。
- 帮 Agent 判断影响范围。

### 9.4 做完后得到什么

例子：

```text
测试失败位置：encrypt_message
调用链：
  test_encrypt_empty_key -> encrypt_message -> gronsfeld
Top-k 候选：
  gronsfeld 排名上升，因为它在失败路径附近并有静态风险信号。
```

### 9.5 面试怎么讲

> Call Graph 解决的是“谁调用谁”的问题。缺陷不一定出现在 traceback 最上层，所以我用调用图把动态失败信号和静态风险信号传播到相关函数，提高函数级定位的解释性。

## 10. 第六步：Program Graph 构建

### 10.1 做了什么

Program Graph 是比 Call Graph 更统一的图结构。它把文件、函数、类、调用、测试、风险信号等都抽象成节点和边。

可以理解成：

```text
File 节点
  -> contains -> Function 节点
Function 节点
  -> calls -> Function 节点
Function 节点
  -> has_signal -> BugSignal 节点
Test 节点
  -> covers/fails_near -> Function 节点
```

### 10.2 怎么做

系统会把前面得到的 repo profile、AST、Call Graph、静态信号和测试证据整合成统一图。

节点可能包括：

- 文件节点。
- 函数节点。
- 类节点。
- 调用节点。
- 静态信号节点。
- 测试证据节点。
- blocker 节点。

边可能包括：

- contains。
- calls。
- imports。
- located_in。
- has_static_signal。
- related_to_failure。

### 10.3 为什么这么做

单独看 AST 只能看到单个文件结构；单独看测试只能看到执行结果；单独看 LLM 输出不可审计。Program Graph 把这些信号统一起来，方便做图评分和解释。

对算法向简历来说，这一步很重要，因为它体现了你不是只做 prompt engineering，而是做了程序分析和图建模。

### 10.4 做完后得到什么

一个函数不再只是“函数名”，而是图中的一个节点，带有多类证据：

```text
函数节点：gronsfeld
  文件：ciphers/gronsfeld_cipher.py
  入边：被测试路径调用
  出边：调用 len/key parsing
  静态信号：missing empty guard
  动态信号：失败测试覆盖
  source_role：application
```

### 10.5 面试怎么讲

> Program Graph 是我把静态结构、调用关系、测试证据和缺陷信号融合到一起的统一表示。它让后续 Top-k 定位可以解释为图上的证据融合，而不是黑盒模型猜测。

## 11. 第七步：静态缺陷信号挖掘

### 11.1 做了什么

静态缺陷信号是对代码中潜在风险模式的检测。常见信号包括：

- 空值未处理。
- 边界条件缺失。
- 除零风险。
- 字典 key 缺失风险。
- 索引越界风险。
- 异常吞掉后无处理。
- 复杂度过高。
- 参数未校验。
- 返回值不一致。

### 11.2 怎么做

系统不是直接说“这个一定是 bug”，而是给函数增加风险 feature。比如：

```text
if 代码存在 dict[key] 访问，但附近没有 key in dict 或 get 默认值：
  增加 signal = possible_missing_key_guard
```

再比如：

```text
if 代码出现 len(x) 作为除数，且没有判断 len(x) == 0：
  增加 signal = possible_zero_division
```

每个 signal 都是“可疑证据”，不是最终定罪。

### 11.3 为什么这么做

真实仓库里通常没有直接告诉你 bug 在哪里。静态信号能给 Agent 一个初始搜索方向。

但静态信号也会有误报，所以它必须和其他证据融合：

- source role。
- 调用图位置。
- 测试失败路径。
- LLM reasoning。
- sandbox validation。

### 11.4 做完后得到什么

例子：

```text
函数：load_user
信号：
  possible_missing_key_guard
原因：
  访问 config["user"]，但未看到默认值或存在性检查。
后续：
  Top-k 分数增加，但仍需测试或上下文验证。
```

### 11.5 面试怎么讲

> 静态规则不是为了直接判定 bug，而是为了提供可解释的风险信号。我把规则输出作为 Top-k 定位的一个 feature，再结合图结构和动态证据做融合。

## 12. 第八步：函数级 Top-k 缺陷定位

### 12.1 做了什么

Top-k 缺陷定位会给函数排序，输出最可疑的前 k 个函数。

它回答的是：

```text
如果当前仓库存在 bug，最应该优先查看哪些函数？
```

### 12.2 怎么做

当前项目的主定位器是 `FaultLocalizer`，它不是只用一个规则分数，而是把每个候选函数拆成多个信号，再做加权融合。候选函数来自 Program Graph 中的非测试函数，也就是：

```text
candidate_functions =
  program_graph.functions
  - 测试函数
  - 测试文件里的函数
```

这样做的原因是：缺陷修复通常应该优先发生在应用源码，而不是测试代码中。测试代码可以提供证据，但默认不应该成为修复目标。

{{DIAGRAM:score_fusion|FinalScore 多信号融合与 Top-k 排名}}

### 12.3 FinalScore 的主公式

当前实现中，每个函数最终会得到一个 `score`，也就是文档里说的 `FinalScore`。主公式是：

```text
FinalScore = clamp(
    w_sbfl     * SBFLScore
  + w_graph    * GraphScore
  + w_static   * StaticScore
  + w_semantic * SemanticScore
  + w_llm      * LLMScore
  - w_risk     * PatchRisk
)
```

其中 `clamp` 的意思是把结果限制在 `[0, 1]` 区间：

```text
clamp(x) = min(1.0, max(0.0, x))
```

### 12.4 两套默认权重

项目会根据是否存在覆盖/测试证据切换权重。

如果 `TestExecutionSummary.has_coverage()` 为真，也就是存在 failed tests、passed tests 或 coverage 信息，使用覆盖证据权重：

| 信号 | 权重 | 含义 |
| --- | ---: | --- |
| `SBFLScore` | 0.30 | 失败测试覆盖相关性，是动态证据中最强的一类 |
| `GraphScore` | 0.25 | 图结构、traceback、测试邻近性、调用影响等 |
| `StaticScore` | 0.15 | 静态规则命中置信度 |
| `SemanticScore` | 0.10 | 函数与失败测试/错误信息的语义重合 |
| `LLMScore` | 0.15 | LLM 对候选函数的可疑度评分 |
| `PatchRisk` | 0.05 | 补丁风险惩罚，注意是减分项 |

公式展开就是：

```text
FinalScore = clamp(
    0.30 * SBFLScore
  + 0.25 * GraphScore
  + 0.15 * StaticScore
  + 0.10 * SemanticScore
  + 0.15 * LLMScore
  - 0.05 * PatchRisk
)
```

如果没有覆盖/测试证据，使用静态优先权重：

| 信号 | 权重 | 含义 |
| --- | ---: | --- |
| `SBFLScore` | 0.00 | 没有失败覆盖证据时不使用 SBFL |
| `GraphScore` | 0.20 | 仍然使用调用图和程序图结构 |
| `StaticScore` | 0.60 | 静态规则成为主要依据 |
| `SemanticScore` | 0.10 | 使用失败信息或目标语义相似度；若没有失败信息则为 0 |
| `LLMScore` | 0.10 | LLM 可用时作为辅助判断 |
| `PatchRisk` | 0.00 | 静态-only 模式默认不扣风险项 |

公式展开就是：

```text
FinalScore = clamp(
    0.20 * GraphScore
  + 0.60 * StaticScore
  + 0.10 * SemanticScore
  + 0.10 * LLMScore
)
```

为什么要切换权重：有测试失败证据时，动态证据通常比静态规则更可靠；没有测试证据时，SBFL 没有意义，只能更多依赖静态规则和结构信息。

### 12.5 StaticScore 如何计算

`StaticScore` 来自静态规则命中的 `BugFinding.confidence`。如果一个函数命中多个规则，不是简单相加，而是使用“概率并集”式合并：

```text
StaticScore = 1 - Π(1 - confidence_i)
```

也可以写成逐步更新：

```text
score = 0
for confidence in matched_rule_confidences:
    score = 1 - (1 - score) * (1 - confidence)
```

例子：

```text
函数 load_config 命中两个规则：
  possible_missing_key_guard: confidence = 0.70
  weak_none_guard: confidence = 0.60

StaticScore = 1 - (1 - 0.70) * (1 - 0.60)
            = 1 - 0.30 * 0.40
            = 0.88
```

为什么不直接相加：如果直接相加，两个 0.70 就会超过 1.0，不方便解释。概率并集式合并能表达“多个独立风险信号共同提高可疑度”，同时自然限制在 0 到 1 之间。

### 12.6 SBFLScore 如何计算

`SBFLScore` 使用 Ochiai 公式。SBFL 的全称是 Spectrum-Based Fault Localization，核心思想是：

```text
如果某个函数经常被失败测试覆盖，而很少被通过测试覆盖，
那么它更可疑。
```

函数级 Ochiai 公式是：

```text
SBFLScore =
  failed_covered / sqrt(total_failed * (failed_covered + passed_covered))
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `total_failed` | 总失败测试数量 |
| `failed_covered` | 覆盖该函数的失败测试数量 |
| `passed_covered` | 覆盖该函数的通过测试数量 |

例子：

```text
total_failed = 2
failed_covered = 2
passed_covered = 6

SBFLScore = 2 / sqrt(2 * (2 + 6))
          = 2 / sqrt(16)
          = 0.50
```

如果一个函数只被失败测试覆盖、不被通过测试覆盖：

```text
total_failed = 2
failed_covered = 2
passed_covered = 0

SBFLScore = 2 / sqrt(2 * 2)
          = 1.00
```

这表示它非常可疑。

项目里还会计算更细粒度的 SBFL 变体：

- `statement_sbfl`：语句级覆盖 Ochiai。
- `branch_sbfl`：分支 outcome 级 Ochiai。
- `path_sbfl`：路径片段级 Ochiai。

这些细粒度信号不会直接作为主公式里的 `SBFLScore`，而是进入 `GraphScore` 的内部组合。

### 12.7 GraphScore 如何计算

`GraphScore` 是最复杂的部分，因为它把测试、traceback、调用图、控制流、数据流、PageRank 和补丁风险统一起来。

当前实现的公式是：

```text
GraphScore = clamp(
    0.16 * traceback_hit
  + 0.16 * test_coverage
  + 0.08 * line_coverage
  + 0.08 * statement_sbfl
  + 0.06 * branch_sbfl
  + 0.06 * path_sbfl
  + 0.07 * data_dependency
  + 0.07 * control_flow
  + 0.14 * proximity
  + 0.07 * centrality
  + 0.07 * pagerank
  + 0.06 * caller_impact
  + 0.04 * module_dependency
  + 0.04 * async_call
  + 0.08 * dynamic_test_evidence
  + 0.12 * static_score
  - 0.10 * patch_risk
)
```

每个分项的含义如下：

| 分项 | 如何得到 | 直觉解释 |
| --- | --- | --- |
| `traceback_hit` | 函数是否出现在 traceback function ids 中，是则 1，否则 0 | traceback 直接指向的函数更可疑 |
| `test_coverage` | 是否被失败测试覆盖，是则 1，否则 0 | 被失败测试覆盖的函数更可疑 |
| `line_coverage` | 失败测试中该函数的最大行覆盖比例 | 覆盖越多，越可能相关 |
| `statement_sbfl` | 语句级 Ochiai 最大值 | 某些语句只被失败测试覆盖时加分 |
| `branch_sbfl` | 分支 outcome 级 Ochiai 最大值 | 某个分支只在失败时走到会加分 |
| `path_sbfl` | 路径片段级 Ochiai 最大值 | 某条执行路径和失败强相关会加分 |
| `data_dependency` | 数据依赖边数量归一化 | 参数流、返回值流、key 流相关越多越可疑 |
| `control_flow` | CFG/control-flow 边数量归一化 | 控制结构复杂度和失败路径可能相关 |
| `proximity` | 到失败测试节点最短路径的 `1/(1+distance)` | 离失败测试越近越可疑 |
| `centrality` | 调用/被测试 degree 归一化 | 图中更中心的函数影响范围更大 |
| `pagerank` | Program Graph 上 PageRank 后归一化 | 被重要节点指向的函数更重要 |
| `caller_impact` | 生产调用者的传递影响分 | 被更多生产代码调用的函数修复影响更大 |
| `module_dependency` | 模块依赖边归一化 | 跨模块依赖中的关键函数更可疑 |
| `async_call` | await 调用边归一化 | 异步调用链中的函数有额外信号 |
| `dynamic_test_evidence` | 动态证据测试覆盖或图距离 | failure overlay / repository test 的额外证据 |
| `static_score` | 静态规则分数 | 静态风险也进入图综合分 |
| `patch_risk` | 调用入度归一化 | 被很多函数调用的核心函数修改风险更高，因此扣分 |

注意：`patch_risk` 在 GraphScore 中已经扣一次，在 FinalScore 中还会作为主公式风险项再扣一次。这表示系统既承认高入度函数可能重要，也提醒高入度函数修改风险更高，不能只因为它中心性高就盲目修。

### 12.8 DynamicScore 在文档里如何对应

文档中之前说的 `DynamicScore` 不是单独一个最终字段，而是分散体现在多个字段里：

- `SBFLScore`：失败测试覆盖和通过测试覆盖的对比。
- `traceback_hit`：traceback 是否命中函数。
- `test_coverage`：失败测试是否覆盖函数。
- `line_coverage`、`statement_sbfl`、`branch_sbfl`、`path_sbfl`：更细粒度的动态覆盖。
- `dynamic_test_evidence`：repository test / failure overlay 等动态证据。
- `proximity`：函数到失败测试节点的图距离。

所以面试时要说得准确：

```text
动态证据不是一个单独的分数，而是通过 SBFLScore 和 GraphScore 内部的多个子信号进入 FinalScore。
```

### 12.9 SemanticScore 如何计算

`SemanticScore` 解决的是“名字和错误上下文是否相似”的问题。它不需要大模型，是一个轻量 token overlap 相似度。

第一步：从失败测试构造 query tokens：

- 失败测试名称。
- 失败测试函数名。
- 失败测试文件名。
- failure message。

第二步：从候选函数构造 document tokens：

- 函数名。
- qualified name。
- 文件名。
- 函数源码。
- 静态规则 id。
- bug type。
- 静态规则 message。

第三步：计算归一化 token overlap：

```text
SemanticScore =
  |query_tokens ∩ document_tokens|
  / sqrt(|query_tokens| * |document_tokens|)
```

例子：

```text
失败测试名：test_empty_key_gronsfeld
失败信息：empty key should not crash
候选函数名：gronsfeld
候选规则：missing_empty_guard

共同 token 包含：
  empty, key, gronsfeld, guard

SemanticScore 会上升。
```

这个分数的作用不是直接判定 bug，而是让“语义上和失败场景更接近”的函数在排序中略微加分。

### 12.10 LLMScore 如何计算

`LLMScore` 只有在两种条件同时满足时才生效：

- `use_llm_score = True`
- 注入了 `llm_scorer`

如果没有 LLM scorer，`LLMScore = 0`。这意味着项目不会因为没有大模型就无法定位；它仍然可以用静态规则、图结构和测试证据排名。

LLM scorer 的输入是：

- Program Graph。
- 静态 findings。
- TestExecutionSummary。
- candidate_function_ids。

输出是：

```text
function_id -> score in [0, 1]
```

系统会对 LLM 输出做 clamp，确保分数不会越界。LLMScore 只是 FinalScore 的一个加权项，不是最终裁判。

### 12.11 PatchRisk 如何计算

`PatchRisk` 用来惩罚修改风险。当前实现中，它主要来自调用入度：

```text
PatchRisk =
  in_degree(function, edge_type="calls")
  / max_in_degree_among_candidates
```

直觉是：

```text
一个函数被很多地方调用，修改它的潜在影响更大。
```

所以 `PatchRisk` 是扣分项：

```text
FinalScore 中：- w_risk * PatchRisk
GraphScore 中：- 0.10 * patch_risk
```

这并不表示高风险函数一定不修，而是表示：如果两个函数其他证据接近，系统更倾向先修范围更小、影响更可控的函数。

### 12.12 手算一个 FinalScore 例子

假设某个函数 `gronsfeld` 的信号如下：

| 信号 | 数值 | 解释 |
| --- | ---: | --- |
| `SBFLScore` | 0.80 | 失败测试强覆盖，少量通过测试也覆盖 |
| `GraphScore` | 0.72 | 接近失败测试、traceback 或动态证据较强 |
| `StaticScore` | 0.60 | 命中边界条件缺失规则 |
| `SemanticScore` | 0.40 | 函数名/错误信息和失败测试有 token 重合 |
| `LLMScore` | 0.70 | LLM 认为该函数较可疑 |
| `PatchRisk` | 0.30 | 有一定调用入度，但不是最高风险 |

因为有测试证据，使用覆盖证据权重：

```text
FinalScore =
    0.30 * 0.80
  + 0.25 * 0.72
  + 0.15 * 0.60
  + 0.10 * 0.40
  + 0.15 * 0.70
  - 0.05 * 0.30

FinalScore =
    0.240
  + 0.180
  + 0.090
  + 0.040
  + 0.105
  - 0.015
  = 0.640
```

如果另一个函数 `helper_parse_key` 的信号是：

```text
SBFLScore = 0.40
GraphScore = 0.45
StaticScore = 0.20
SemanticScore = 0.30
LLMScore = 0.30
PatchRisk = 0.10
```

则：

```text
FinalScore =
    0.30 * 0.40
  + 0.25 * 0.45
  + 0.15 * 0.20
  + 0.10 * 0.30
  + 0.15 * 0.30
  - 0.05 * 0.10
  = 0.3325
```

所以排序会是：

```text
Rank 1: gronsfeld        FinalScore = 0.6400
Rank 2: helper_parse_key FinalScore = 0.3325
```

### 12.13 做完后得到什么

输出中的每个 `FaultLocalizationResult` 会包含：

- `function_id`
- `function_name`
- `file_path`
- `start_line`
- `end_line`
- `score`
- `rank`
- `signals`
- `findings`
- `reason`

其中 `signals` 会保留分项分数，例如：

```text
signals:
  sbfl: 0.8000
  graph: 0.7200
  static: 0.6000
  semantic: 0.4000
  llm: 0.7000
  risk: 0.3000
  traceback_hit: 1.0000
  proximity: 0.5000
  dynamic_test_evidence: 1.0000
  patch_risk: 0.3000
```

这就是可解释性的来源：不是只给一个排名，还能说明为什么排到这里。

### 12.14 为什么要 Top-k，而不是直接 Top-1

原因有三个：

- 静态分析和测试证据都可能不完整。
- Python 动态调用导致图不一定完全准确。
- 自动修复需要候选集，不能只依赖一个函数。

Top-k 的价值是给 Agent 和 LLM 一个优先级列表，让后续 patch generation 聚焦在更可能的区域。

### 12.15 具体输出例子

例子：

```text
Rank 1: gronsfeld
  文件：ciphers/gronsfeld_cipher.py
  证据：动态失败路径 + 静态边界信号
  处理建议：优先生成 guarded patch

Rank 2: helper_parse_key
  证据：调用链相关，但静态信号较弱
  处理建议：作为上下文参考
```

### 12.16 面试怎么讲

> 我做的是函数级 Top-k fault localization，而不是文件级粗粒度定位。核心是一个多信号融合的 FinalScore：有覆盖证据时主要融合 SBFL、Graph、Static、Semantic、LLM 和 PatchRisk；没有覆盖证据时切换到静态优先权重。StaticScore 使用规则置信度的概率并集合并，SBFL 使用 Ochiai，GraphScore 融合 traceback、覆盖、图距离、PageRank、数据依赖、控制流、调用影响和动态证据，最后按分数降序输出 Top-k suspicious functions。这样 LLM 修复不会在全仓库盲猜，而是围绕可解释的高风险函数生成 patch。

## 13. 第九步：测试环境诊断

### 13.1 做了什么

Agent 会诊断仓库的测试环境：

- 有没有测试目录。
- 有没有 pytest 配置。
- 有没有 unittest 风格测试。
- 有没有 tox/nox。
- 有没有依赖安装文件。
- 是否能构造测试命令。
- 是否应该执行测试。
- 执行失败是代码失败、依赖失败还是环境失败。

### 13.2 怎么做

系统会读取：

- `pyproject.toml`
- `pytest.ini`
- `tox.ini`
- `noxfile.py`
- `setup.cfg`
- `requirements.txt`
- `tests/` 目录

然后推断可能的命令：

```text
python -m pytest
python -m pytest tests
python -m unittest discover
tox
nox
```

在受控执行模式下，它会运行测试并记录 return code、stdout/stderr 摘要、失败数、通过数和 blocker。

### 13.3 为什么这么做

补丁是否正确不能靠模型主观判断，必须靠测试或 oracle 验证。但真实仓库测试环境复杂：

- 有的仓库依赖很多。
- 有的测试需要网络、数据库或系统服务。
- 有的仓库测试本身就有历史失败。
- 有的仓库没有测试命令。

所以 Agent 需要先区分“测试失败是因为 bug”还是“环境不可复现”。

### 13.4 做完后得到什么

常见结果包括：

```text
test command discovered = true
test runner = pytest
test result = pass
dynamic evidence usable = false
reason = passing_tests
```

或者：

```text
test command discovered = false
blocker = no_test_command
selected action = expand_static_candidate_search
```

### 13.5 面试怎么讲

> 测试环境诊断是为了避免把环境问题误判成代码 bug。Agent 会先发现 pytest/unittest/tox/nox 信号，再判断测试结果是否能作为动态证据。如果只是 passing tests，就作为 regression guard；如果是依赖失败，就报告环境 blocker；只有 failing evidence 可用时才进入修复。

## 14. 第十步：动态证据融合

### 14.1 做了什么

动态证据来自实际执行结果，比如：

- failing test。
- traceback。
- 失败函数路径。
- failure overlay。
- patch validation 结果。
- regression test 结果。

系统会把这些证据和静态图融合，影响 Top-k 排名和 Agent 下一步动作。

### 14.2 怎么做

举例：

```text
失败测试指向 encrypt_message。
Call Graph 显示 encrypt_message 调用 gronsfeld。
gronsfeld 有 missing empty-key guard 信号。
因此 gronsfeld 的 FinalScore 上升。
```

如果测试全部通过：

```text
测试通过不是 bug 证据。
它只能作为 regression guard。
Agent 不应强行修复。
```

### 14.3 为什么这么做

静态信号可能误报，动态证据能显著提高定位可信度。但动态证据也有边界：passing tests 不能证明没有 bug，更不能告诉你修哪里。

因此 Agent 要区分：

- usable failing evidence。
- passing regression evidence。
- environment blocker。
- insufficient oracle。

### 14.4 做完后得到什么

例子：

```text
dynamic_evidence_level = failing_overlay
Top-1 = gronsfeld
repairability = repair_ready
next_action = generate_patch_candidates
```

或者：

```text
dynamic_evidence_level = passing_tests
repairability = not_repair_ready
next_action = extend_failure_overlay_or_provide_bug_report
```

### 14.5 面试怎么讲

> 我把测试结果分成不同证据等级，而不是简单通过或失败。只有可用 failing evidence 才推动自动修复；passing tests 会变成回归保护；环境失败会变成 blocker。这样 Agent 的行为更可信。

## 15. 第十一步：AgentController 闭环

### 15.1 做了什么

AgentController 是整个系统的决策核心。它负责把前面所有观察结果转成下一步动作。

核心闭环是：

```text
Observe -> Plan -> Act -> Verify -> Reflect -> Replan
```

{{DIAGRAM:agent_loop|Agent Controller 的 Observe、Plan、Act、Verify、Reflect、Replan 闭环}}

### 15.2 Observe：观察什么

Observe 阶段读取：

- repo profile。
- source import 状态。
- AST/Graph 是否就绪。
- Top-k 排名。
- test diagnosis。
- dynamic evidence。
- patch validation 状态。
- LLM 可用性。
- session memory。
- 用户新约束。

它的目标是构造一个“当前状态对象”。

### 15.3 Plan：规划什么

Plan 阶段决定下一步 action，例如：

- `discover_repository_tests`
- `run_repository_tests_with_checkout`
- `expand_static_candidate_search`
- `adjust_source_filters`
- `generate_patch_candidates`
- `validate_patch_candidates`
- `extend_failure_overlay_or_provide_bug_report`
- `report_blocker`

如果启用了 LLM replanner，LLM 可以提出建议，但不能直接越过安全门。

### 15.4 Act：执行什么

Act 阶段只执行 action registry 中允许的动作。比如：

- 运行测试。
- 生成报告。
- 生成补丁候选。
- 验证补丁。
- 更新 session memory。
- 输出下一步命令。

### 15.5 Verify：验证什么

Verify 阶段检查动作是否真的成功：

- 文件是否生成。
- 测试是否运行。
- return code 是否符合预期。
- patch 是否应用成功。
- sandbox pytest 是否通过。
- 报告是否完整。

### 15.6 Reflect：反思什么

Reflect 阶段关注失败原因：

- 是环境失败吗？
- 是测试 oracle 不足吗？
- 是 patch 编译失败吗？
- 是 patch 没解决问题吗？
- 是 LLM 生成了越界动作吗？
- 是重复生成了旧失败补丁吗？

### 15.7 Replan：重新规划什么

Replan 阶段根据反思结果决定：

- 继续尝试 refined patch。
- 更换 repair strategy。
- 缩小分析范围。
- 请求用户提供 failing test。
- 停止并输出 blocker。
- 进入评估报告。

### 15.8 面试怎么讲

> AgentController 是这个项目区别于普通 workflow 的关键。它不是固定执行所有步骤，而是在每轮观察仓库状态后规划下一步动作，并在执行后验证、失败后反思、再重新规划。这个闭环让系统能处理真实仓库里的不确定性。

## 16. 第十二步：LLM Planner 与安全门控

### 16.1 LLM 在哪里发挥作用

LLM 可以参与：

- Planner/Replanner：根据当前状态建议下一步 action。
- Patch generation：生成补丁候选。
- Test reflection：解释测试失败原因。
- Patch judge：评估候选补丁质量。
- Chat response：在多轮对话中解释状态和下一步。

但 LLM 不是无约束主控。它的建议必须经过 action registry 和风险策略检查。

{{DIAGRAM:safety_gate|LLM 规划、安全门控与 Controller fallback}}

### 16.2 为什么不让 LLM 完全判断

如果让 LLM 完全主控，风险很高：

- 可能建议不存在的 action。
- 可能绕过测试直接声称修复成功。
- 可能修改测试而不是修源码。
- 可能执行危险命令。
- 可能在证据不足时过度自信。

所以系统采用“LLM 建议 + 规则安全边界”的结构：

```text
LLM 负责推理和建议。
Controller 负责执行边界。
Safety Gate 负责拒绝越界动作。
Sandbox 负责验证结果。
```

### 16.3 一个真实例子

在 `pypa/sampleproject` 的 LLM run 中，LLM 可能建议：

```text
report_blocker_and_request_external_input
```

但如果这个 action 没在 action registry 中注册，安全门会拒绝：

```text
rejected_reason = llm_recommended_action_not_registered
```

然后 Controller 会选择一个可执行的 fallback action，例如：

```text
extend_failure_overlay_or_provide_bug_report
```

这个例子很适合面试，因为它说明：

- LLM 参与了规划。
- LLM 不是随便说了就执行。
- 安全门能拒绝未注册动作。
- Controller 能回退到可审计动作。

### 16.4 做完后得到什么

报告中通常会记录：

- LLM Planner 是否启用。
- LLM 建议的 action。
- 建议是否通过安全门。
- 被拒绝的原因。
- 最终采用的 action。
- fallback 逻辑。

### 16.5 面试怎么讲

> 我没有让 LLM 完全接管执行，而是把它作为 planner 和 reasoning tool。LLM 可以基于当前状态提出下一步动作，但动作必须存在于 action registry，并通过风险策略检查。这样既体现了智能规划，又保证工程安全和可审计。

## 17. 第十三步：补丁生成

### 17.1 做了什么

当系统判断具备修复条件时，会生成 patch candidates。补丁生成可以有三种模式：

- rule：基于规则模板生成。
- llm：由大模型根据上下文生成。
- hybrid：规则候选和 LLM 候选结合。

### 17.2 怎么做

补丁生成不会直接让 LLM 改整个仓库，而是给它结构化上下文：

- Top-k 可疑函数。
- 函数边界。
- 静态风险信号。
- 失败测试摘要。
- traceback 摘要。
- 用户约束。
- 历史失败 patch 指纹。
- 禁止修改测试的规则。
- 需要保持 API 兼容的要求。

LLM 输出候选补丁后，还要经过格式检查、安全检查和 sandbox 验证。

### 17.3 为什么这么做

直接让 LLM 修整个仓库会有几个问题：

- 上下文太大。
- 容易改错文件。
- 容易生成不可应用 diff。
- 容易改测试绕过失败。
- 无法解释为什么改这里。

Top-k + patch context 的方式能把 LLM 的搜索空间限制在高可疑函数附近，提高可靠性。

### 17.4 做完后得到什么

补丁候选通常会有：

- candidate id。
- target file。
- target function。
- generation mode。
- risk level。
- apply status。
- validation status。
- failure reason。

例子：

```text
candidate = missing_len_zero_guard
target = gronsfeld
mode = rule/reflection
status = validation_pass
reason = target pytest passed
```

### 17.5 面试怎么讲

> 补丁生成不是让 LLM 自由改代码，而是基于 Top-k 定位和失败证据构造受控上下文。生成候选后必须经过 patch apply、安全检查和 sandbox pytest。hybrid 模式能结合规则补丁的稳定性和 LLM 的泛化能力。

## 18. 第十四步：Sandbox Validation

### 18.1 做了什么

Sandbox Validation 是验证补丁是否真正有效的阶段。

它会检查：

- patch 是否能应用。
- 代码是否能解析。
- 目标测试是否通过。
- 回归测试是否通过或是否存在已知 caveat。
- 是否修改了不允许修改的文件。
- 是否超时。
- 是否产生环境错误。

### 18.2 怎么做

常见流程：

1. 复制或隔离仓库工作区。
2. 应用 patch。
3. 运行目标测试。
4. 运行必要回归测试。
5. 收集 stdout/stderr、return code 和失败摘要。
6. 输出 validation report。

### 18.3 为什么这么做

补丁是否正确不能靠 LLM 自评。一个 patch 看起来合理，但可能：

- 语法错误。
- 没有修复目标失败。
- 引入新失败。
- 修改了公共 API。
- 只是掩盖测试。

Sandbox 是最终事实来源。

### 18.4 做完后得到什么

例子：

```text
patch apply = pass
target pytest = pass
regression guard = caveat
final status = patch_validation_reflection_success
```

如果失败：

```text
patch apply = pass
target pytest = fail
failure type = assertion_error
next action = reflect_and_generate_refined_candidate
```

### 18.5 面试怎么讲

> Sandbox Validation 是我判断修复是否成立的最终标准。LLM 生成 patch 后不直接采纳，而是在隔离环境中应用补丁并运行测试，把执行结果写入报告。失败结果会进入 reflection，而不是被忽略。

## 19. 第十五步：Reflection Loop

### 19.1 做了什么

Reflection Loop 用于处理补丁失败。它会读取失败信息，并生成下一轮修复策略。

它关注：

- 哪个候选失败。
- 失败发生在 apply、parse、target test 还是 regression test。
- stdout/stderr 中有什么关键信息。
- 旧 patch 改了什么。
- 是否重复了过去失败的修复方向。
- 是否需要换策略。

### 19.2 怎么做

简化流程：

```text
读取失败 patch
读取测试失败摘要
分类失败原因
生成 reflection note
更新 repair memory
生成 refined candidate
再次 sandbox validation
```

### 19.3 为什么这么做

大模型或规则生成的第一个 patch 不一定成功。如果失败后直接停止，Agent 只是一轮工具调用。Reflection 让它具备持续改进能力。

### 19.4 具体例子：TheAlgorithms/Python

`TheAlgorithms/Python` 的 gronsfeld case 展示了这一点：

```text
Top-1 定位：gronsfeld
初始 patch：失败
Reflection：分析失败原因，生成 depth=1 refined patch
验证结果：目标 pytest 通过
最终状态：patch validation/reflection success
```

这里不是“模型说成功”，而是 patch 经过 sandbox 验证后成功。

### 19.5 面试怎么讲

> Reflection Loop 体现了 Agent 的自我修复能力。初始补丁失败后，系统不会简单结束，而是把失败输出、旧 patch 和用户约束写入 repair memory，再生成 refined candidate 并重新验证。

## 20. 第十六步：记忆系统与多轮对话

### 20.1 做了什么

记忆系统会保存一次 repo session 中的重要信息：

- 仓库状态。
- 分析结果。
- Top-k 排名。
- 测试诊断。
- 失败 patch。
- 用户约束。
- repair strategy。
- 对话历史。
- 下一步建议。

{{DIAGRAM:memory_chat|多轮对话、Session Memory 与下一步动作}}

### 20.2 怎么做

每次 Agent 分析会生成 session 文件。后续对话可以通过 session id 恢复上下文。

例如第一轮：

```text
分析 pypa/sampleproject，生成 agent_session.json 和 agent_memory.json。
```

第二轮用户说：

```text
解释上一次为什么没有生成补丁。
```

Agent 不需要重新分析全部仓库，而是读取 session memory，回答：

```text
因为当前只有 passing tests，没有可用 failing evidence，所以不满足自动修复条件。
```

第三轮用户说：

```text
不要修改公共 API，继续尝试 Top-1 函数。
```

Agent 会把“不要修改公共 API”写入用户约束，后续 patch generation 会读取这个约束。

### 20.3 为什么这么做

代码修复任务通常不是单轮完成。用户可能会逐步补充：

- 新 failing test。
- bug report。
- 不能修改的文件。
- API 兼容要求。
- 想优先分析的模块。

没有记忆系统，每一轮都要从零开始，且容易重复生成同类失败补丁。

### 20.4 当前多轮对话的边界

当前多轮对话更像“围绕 repo session 的任务型对话”，不是完全自由的通用聊天窗口。

它更适合：

- 解释上一次分析结果。
- 修改约束。
- 继续修复。
- 重新运行测试。
- 生成报告。
- 改变修复策略。

它不适合无限开放闲聊，也不会无边界执行任意命令。

### 20.5 面试怎么讲

> 记忆系统保存的是 repo session 级别的工程状态，包括失败 patch、用户约束、测试摘要和 repair strategy。多轮对话不是普通闲聊，而是围绕同一个仓库持续推进分析和修复。

## 21. 第十七步：报告系统

### 21.1 做了什么

报告系统把 Agent 的运行过程变成可审计产物。

主要报告包括：

- `github_repo_intelligence.md`
- `agent_decision_report.md`
- `agent_execution_trace.md`
- `agent_memory_report.md`
- `agent_session_report.md`

### 21.2 怎么看报告

建议按这个顺序看：

1. 先看 `github_repo_intelligence.md`，了解最终结论。
2. 再看 `agent_decision_report.md`，理解 Controller 为什么这么选。
3. 再看 `agent_execution_trace.md`，确认动作是否真的执行。
4. 最后看 `agent_memory_report.md`，理解多轮记忆和历史失败。

### 21.3 每份报告回答什么问题

| 报告 | 回答的问题 | 面试用途 |
| --- | --- | --- |
| `github_repo_intelligence.md` | 仓库最终分析结果是什么 | 展示项目可运行 |
| `agent_decision_report.md` | Agent 为什么选择这个动作 | 展示 Agent 决策闭环 |
| `agent_execution_trace.md` | 每一步是否真的执行 | 回答“是不是只有模块没有跑” |
| `agent_memory_report.md` | 多轮记忆保存了什么 | 展示 memory 能力 |
| `agent_session_report.md` | 当前 session 如何恢复 | 展示多轮延续 |

### 21.4 为什么报告重要

Agent 项目如果只在终端打印结果，很难证明它真的做了完整闭环。报告系统让每一步都有证据：

- 输入是什么。
- 观察到什么。
- 计划是什么。
- 执行了什么。
- 验证结果是什么。
- 失败原因是什么。
- 下一步是什么。

### 21.5 面试怎么讲

> 我特别强调可审计报告，因为代码修复 Agent 不能只给最终一句话。报告会记录 repo profile、Top-k 定位、测试诊断、Controller 决策、action trace、patch validation 和 memory，让面试官能追溯每一步。

## 22. 三个核心样例怎么讲

### 22.1 样例 A：pytest-dev/pluggy

这个样例展示“正常 Python 仓库 + pytest 可执行 + 测试通过”。

做了什么：

- 识别 GitHub URL。
- 识别 `src/` layout。
- 找到 pytest 测试。
- 执行测试。
- 得到 passing tests。
- 输出 regression guard。

结果怎么理解：

```text
测试通过说明当前没有可用 failing evidence。
Agent 不应该为了展示修复而编造 bug。
它会把通过测试作为回归保护，并要求提供 failing test 或 bug report 才能进入修复。
```

面试讲法：

> `pluggy` 例子说明系统能处理真实 `src` layout 和 pytest 项目。测试通过时，Agent 把它作为 regression guard，而不是强行生成 patch。这体现了系统的证据意识。

### 22.2 样例 B：pypa/sampleproject

这个样例展示“可分析仓库 + 测试/环境诊断 + 动态证据不足”。

做了什么：

- 识别 owner/repo。
- 分析 Python 包结构。
- 诊断测试入口。
- 运行或规划测试。
- 得到 passing tests 或动态证据不足。
- 输出需要 failing evidence 的 blocker。

结果怎么理解：

```text
当前可以做静态定位，但还不能安全修复。
因为没有失败测试或明确 bug report，patch generation 不应该直接启动。
```

面试讲法：

> `sampleproject` 体现的是 blocker 处理能力。Agent 能继续分析到 Top-k 和测试诊断，但在缺少 failing evidence 时停止并说明需要什么外部输入，这比盲目修复更符合真实工程要求。

### 22.3 样例 C：TheAlgorithms/Python

这个样例展示“Top-k 定位 + patch validation + reflection 成功”。

做了什么：

- 使用 gronsfeld case。
- Top-1 定位到 `gronsfeld`。
- 生成初始补丁。
- 初始补丁失败。
- Reflection 生成 refined patch。
- Sandbox 验证目标测试通过。

结果怎么理解：

```text
这是最能体现自动修复闭环的样例。
它不是一次生成 patch 就结束，而是失败后反思，再生成 refined candidate，并通过测试验证。
```

面试讲法：

> `TheAlgorithms/Python` 的 gronsfeld case 展示了完整修复闭环。Top-k 定位到目标函数后，初始补丁失败，reflection 根据失败输出生成 refined patch，最终 sandbox target pytest 通过。这个例子能证明 Agent 具备失败反馈驱动的自我修复能力。

### 22.4 样例 D：octocat/Hello-World

这个样例展示“非 Python 或无可分析源码 blocker”。

做了什么：

- 输入 GitHub 仓库。
- 尝试发现 Python 源码。
- 未发现可分析源码。
- Agent 输出 source import blocker。
- 建议调整分析范围或更换目标。

结果怎么理解：

```text
这不是失败，而是正确拒绝。
Agent 不能把非 Python 仓库伪装成分析成功。
```

面试讲法：

> `Hello-World` 例子说明系统有边界判断能力。没有 Python 源码时，Agent 会报告 source blocker，而不是虚构分析结果。

## 23. 如何运行项目

### 23.1 普通 Agent 分析

```powershell
cd <repository_root>
python -m code_intelligence_agent agent pypa/sampleproject outputs_smoke\sampleproject_agent_run --execution-profile agent-auto --preset mining --format markdown
```

你应该关注：

- 是否生成输出目录。
- 是否生成 Markdown 报告。
- 是否出现 AgentController 决策。
- 是否记录 blocker 或 next action。

### 23.2 接入 LLM 的 Agent 分析

环境变量示例：

```powershell
$env:CIA_LLM_REPLAN_ENABLED="1"
$env:CIA_REPLAN_LLM_PROVIDER="deepseek"
$env:CIA_REPLAN_LLM_MODEL="<model_name>"
$env:CIA_REPLAN_LLM_API_KEY="<your_api_key>"

$env:CIA_LLM_PROVIDER="deepseek"
$env:CIA_LLM_MODEL="<model_name>"
$env:CIA_LLM_API_KEY="<your_api_key>"

$env:CIA_JUDGE_PROVIDER="deepseek"
$env:CIA_JUDGE_MODEL="<model_name>"
$env:CIA_JUDGE_API_KEY="<your_api_key>"
```

运行命令：

```powershell
python -m code_intelligence_agent agent pypa/sampleproject outputs_smoke\sampleproject_llm_agent_run --execution-profile agent-auto --preset mining --repository-patch-generation-mode hybrid --repository-test-reflection-mode llm --patch-judge-mode llm --auto-controller-max-actions 2 --format markdown
```

注意：

- API key 不要写入 README、代码、测试或报告。
- LLM 可能比较慢，因为它要等待网络请求。
- `--auto-controller-max-actions 2` 会限制自动动作数量，避免无限执行。
- 如果没有 failing evidence，LLM 也不应该强行生成补丁。

### 23.3 终端多轮对话

第一步先生成 session：

```powershell
python -m code_intelligence_agent agent pypa/sampleproject outputs_smoke\sampleproject_agent_session --execution-profile agent-auto --preset mining --format markdown
```

然后通过 session 继续：

```powershell
python -m code_intelligence_agent chat --session <session_id> --message "解释上一次为什么没有生成补丁" --format markdown
```

继续添加约束：

```powershell
python -m code_intelligence_agent chat --session <session_id> --message "后续不要修改公共 API" --format markdown
```

要求重跑测试：

```powershell
python -m code_intelligence_agent chat --session <session_id> --message "重新运行 pytest" --execute --format markdown
```

### 23.4 每次运行后怎么判断是否成功

不要只看终端最后一行。应该检查：

- 输出目录是否存在。
- `github_repo_intelligence.md` 是否生成。
- `agent_decision_report.md` 是否记录 planned action。
- `agent_execution_trace.md` 是否记录 executed/blocked/verified。
- `agent_memory_report.md` 是否记录 session memory。
- 如果有 patch，是否有 validation 结果。

## 24. 面试高频问题与参考回答

### Q1：为什么你这个是 Agent，不是 workflow？

回答：

> Workflow 是固定流程，而我的系统会根据观察到的仓库状态选择下一步动作。比如没有 Python 源码就调整 source filter，没有测试命令就扩展静态搜索，测试通过就作为 regression guard，有 failing evidence 才进入 patch generation。它有 Observe、Plan、Act、Verify、Reflect、Replan 闭环，也有 session memory 和多轮延续，所以是受控代码智能 Agent。

### Q2：LLM 在项目中到底负责什么？

回答：

> LLM 主要负责规划增强、补丁生成、测试失败反思和候选评判。它不是唯一控制器，Controller 会把 LLM 建议放进 action registry 和 safety gate 检查。这样 LLM 负责语义推理，规则和 sandbox 负责安全边界与事实验证。

### Q3：为什么不让 LLM 完全规划？

回答：

> 代码修复涉及文件修改和命令执行，完全交给 LLM 风险太高。它可能建议不存在的动作、修改测试、绕过验证或在证据不足时过度自信。所以我采用 LLM planner + rule fallback + safety gate 的结构，既使用大模型推理能力，又保证动作可控。

### Q4：Top-k 定位有什么算法价值？

回答：

> Top-k 定位把静态规则、图结构、动态证据和 source role 融合成函数级排序。它缩小了 LLM 修复搜索空间，也让修复过程可解释。相比直接让 LLM 读全仓库，Top-k 更稳定、更省 token，也更适合工程验证。

### Q5：如果没有 failing test 怎么办？

回答：

> 没有 failing test 时，系统不会强行修复。它可以做静态分析和 Top-k 定位，但会把修复阶段标记为 evidence insufficient，并请求用户提供 failing test、bug report 或 failure overlay。passing tests 只能作为 regression guard。

### Q6：Sandbox 为什么重要？

回答：

> 因为 patch 是否正确必须由执行结果验证。LLM 生成的补丁可能语法错误、没修到问题、引入回归或修改测试。Sandbox 会隔离应用 patch 并运行目标测试和必要回归测试，结果才是是否采纳补丁的依据。

### Q7：Reflection 怎么体现？

回答：

> 初始 patch 失败后，系统会读取失败类型、stdout/stderr 摘要和旧 patch 指纹，更新 repair memory，再生成 refined candidate。`TheAlgorithms/Python` 的 gronsfeld case 就展示了初始补丁失败后 reflection 生成下一轮候选并通过目标 pytest。

### Q8：Memory 系统有什么用？

回答：

> Memory 保存 repo session 的历史状态，包括用户约束、失败 patch、测试摘要和 repair strategy。多轮对话时 Agent 不需要从零开始，也能避免重复生成同类失败补丁。

### Q9：项目目前的边界是什么？

回答：

> 当前更适合 Python GitHub 仓库的智能分析、可审计定位和受控修复。它不是保证任意仓库自动修复。复杂依赖、缺少 failing evidence、非 Python 仓库、需要外部服务的测试都会被报告为 blocker。

### Q10：你觉得项目最有算法深度的地方在哪里？

回答：

> 我会重点讲三点：第一是 AST/Call Graph/Program Graph 的程序结构建模；第二是融合静态规则、图结构和动态证据的函数级 Top-k fault localization；第三是补丁生成后的 sandbox validation 与 reflection loop。LLM 是其中一个智能组件，但项目不是纯 prompt，而是程序分析 + 图建模 + Agent 控制闭环。

## 25. 从 0 学习的 10 天路线

### 第 1 天：跑通一次 Agent

目标：

- 运行 `pypa/sampleproject`。
- 找到输出目录。
- 打开最终报告。

你要理解：

- 输入是什么。
- 输出了哪些报告。
- Agent 是否进入修复。
- 如果没有修复，原因是什么。

完成标准：

```text
能用自己的话解释一次运行从输入到输出发生了什么。
```

### 第 2 天：理解仓库发现和源码筛选

目标：

- 比较 `pypa/sampleproject`、`pytest-dev/pluggy`、`octocat/Hello-World`。
- 理解为什么有的仓库可分析，有的仓库会 blocker。

你要理解：

- owner/repo 和 GitHub URL 如何统一。
- source role 为什么重要。
- `src/` layout 如何识别。

完成标准：

```text
能解释为什么 Hello-World 不应该被伪装成 Python 分析成功。
```

### 第 3 天：理解 AST

目标：

- 理解函数、调用、复杂度和静态信号如何从代码结构中来。

你要理解：

- AST 和字符串搜索的区别。
- 为什么 AST 是 Call Graph 和 Top-k 的基础。

完成标准：

```text
能举一个边界条件缺失如何被 AST signal 捕获的例子。
```

### 第 4 天：理解 Call Graph 和 Program Graph

目标：

- 理解调用图和统一程序图。

你要理解：

- caller/callee。
- 图节点和边。
- 图结构如何辅助定位。

完成标准：

```text
能解释为什么 bug 不一定在 traceback 顶层函数。
```

### 第 5 天：理解静态缺陷信号和 Top-k

目标：

- 理解 StaticRuleScore、GraphScore、DynamicScore 和 FinalScore 的概念。

你要理解：

- 静态信号不是最终 bug 结论。
- Top-k 为什么比 Top-1 更合理。

完成标准：

```text
能讲清楚 Top-k 如何缩小 LLM 修复搜索空间。
```

### 第 6 天：理解测试诊断和动态证据

目标：

- 理解 pytest/unittest/tox/nox 发现。
- 理解 passing tests、failing tests、environment blocker 的区别。

完成标准：

```text
能解释为什么 passing tests 不能直接触发修复。
```

### 第 7 天：理解 AgentController

目标：

- 掌握 Observe -> Plan -> Act -> Verify -> Reflect -> Replan。

完成标准：

```text
能拿 pypa/sampleproject 举例说明每个阶段分别做了什么。
```

### 第 8 天：理解 LLM Planner 和安全门控

目标：

- 理解 LLM 在规划中的作用和限制。
- 理解 action registry 和 safety gate。

完成标准：

```text
能解释为什么 LLM 推荐未注册 action 时会被拒绝。
```

### 第 9 天：理解 Patch、Sandbox 和 Reflection

目标：

- 学习 `TheAlgorithms/Python` gronsfeld case。

完成标准：

```text
能讲清楚初始 patch 失败后 reflection 如何生成 refined patch。
```

### 第 10 天：准备简历和面试讲解

目标：

- 准备 1 分钟项目概述。
- 准备 3 分钟技术细节。
- 准备 5 分钟完整案例。
- 准备边界说明。

完成标准：

```text
能不看源码讲完整个项目的动机、架构、算法、Agent 闭环、LLM 作用和工程边界。
```

## 26. 简历与面试的对应关系

### 26.1 简历一句话

```text
构建面向 Python GitHub 仓库的代码智能 Agent，支持仓库发现、AST/Call Graph/Program Graph 建模、静态缺陷信号挖掘、函数级 Top-k 缺陷定位、测试诊断、LLM 辅助补丁生成、sandbox 验证、reflection 自修复和 session memory 多轮分析。
```

### 26.2 简历项目要点

可以写：

- 设计并实现面向 Python GitHub 仓库的 Code Intelligence Agent，支持 GitHub URL/owner-repo/local path 输入，自动完成源码筛选、repo profile 和测试环境诊断。
- 基于 AST、Call Graph 和 Program Graph 构建函数级程序表示，融合静态规则、图结构和动态测试证据，实现 Top-k fault localization。
- 接入 LLM Planner/Replanner 和 hybrid patch generation，通过 action registry、risk policy 和 sandbox validation 限制模型越权行为。
- 实现 Observe -> Plan -> Act -> Verify -> Reflect -> Replan 控制闭环，支持失败补丁反思、refined patch 生成和 session memory 多轮延续。
- 沉淀公开 GitHub 仓库样例报告，覆盖 passing tests、无 Python 源码、无测试命令、环境 blocker、patch validation 和 reflection success 等场景。

### 26.3 面试时不要这么说

不要说：

```text
我的 Agent 可以自动修复任意 GitHub 仓库。
```

应该说：

```text
我的 Agent 可以对任意 Python GitHub 仓库做智能分析和受控修复尝试；当缺少 failing evidence、测试环境不可复现或仓库不满足条件时，会输出 blocker 和下一步动作，而不是编造修复结果。
```

## 27. 如何从一份报告反推完整流程

这一节适合你在不读源码的情况下学习项目。你拿到 `github_repo_intelligence.md` 后，可以按下面顺序反推 Agent 每一步做了什么。

### 27.1 先看仓库输入

你要找的信息：

- 输入仓库是什么。
- 是 GitHub URL、owner/repo 还是本地路径。
- 有没有指定 include/exclude。
- 有没有指定 execution profile。
- 有没有启用 LLM。

你要能解释：

```text
这一步确定了分析边界。
如果仓库、分支、文件范围不明确，后续所有结果都不可审计。
```

例子：

```text
repo = pypa/sampleproject
execution_profile = agent-auto
preset = mining
```

含义：

```text
系统会用 Agent 自动控制策略分析该仓库，并偏向挖掘/定位模式，而不是只做最小静态扫描。
```

### 27.2 再看 repo profile

你要找的信息：

- 是否识别到 Python 源码。
- 是否识别到 `src/` layout。
- 是否识别到测试目录。
- 是否有 pyproject/tox/nox/pytest 配置。
- 是否存在 source import blocker。

你要能解释：

```text
repo profile 是 Agent 的仓库画像。
它决定后续能不能继续 AST、图建模和测试诊断。
```

例子：

```text
pytest-dev/pluggy:
  src layout = true
  pytest signal = true
  application source = src/pluggy
```

含义：

```text
这是一个结构比较标准的 Python 库项目，适合继续做结构建模和测试发现。
```

### 27.3 看 AST/Graph 是否 ready

你要找的信息：

- AST 是否解析成功。
- 函数数量是否大于 0。
- Call Graph 是否生成。
- Program Graph 是否可用。

你要能解释：

```text
如果 graph ready，说明系统已经把仓库从文件级提升到函数和调用关系级。
这一步是算法深度的基础。
```

例子：

```text
program_graph_available = true
repo_graph_ready = true
```

含义：

```text
后续 Top-k 排名不是纯文本猜测，而是可以利用函数节点、调用边和静态信号。
```

### 27.4 看 Top-k suspicious functions

你要找的信息：

- Top-1 是哪个函数。
- 排名靠前函数属于应用源码还是测试代码。
- 分数来源是什么。
- 是否有静态信号、图信号或动态信号。

你要能解释：

```text
Top-k 是修复搜索空间。
排名越高，越优先给 LLM 或规则生成器作为 patch target。
```

例子：

```text
TheAlgorithms/Python:
  Top-1 = gronsfeld
  evidence = dynamic failure + static guard signal
```

含义：

```text
这个函数同时靠近失败路径，并且有边界处理风险，所以适合作为修复目标。
```

### 27.5 看测试诊断

你要找的信息：

- 发现了什么测试 runner。
- 是否执行了测试。
- return code 是什么。
- 是 passing tests、failing tests 还是 environment blocker。
- 动态证据是否 usable。

你要能解释：

```text
测试结果决定是否能进入自动修复。
passing tests 不是 bug oracle；failing tests 才可能推动 patch generation。
```

例子：

```text
dynamic_evidence_not_usable:passing_tests
```

含义：

```text
测试可以作为回归保护，但不能说明当前应该修哪里。
```

### 27.6 看 Agent selected action

你要找的信息：

- Controller 当前阶段是什么。
- blocker 是什么。
- selected action 是什么。
- next action 是什么。
- LLM 建议是否被采纳。

你要能解释：

```text
selected action 是 Agent 决策的核心证据。
它说明系统不是固定 workflow，而是在根据当前状态选择下一步。
```

例子：

```text
blocker = dynamic_evidence_not_usable
selected action = extend_failure_overlay_or_provide_bug_report
```

含义：

```text
Agent 认为继续修复缺少证据，因此要求扩展 failure overlay 或提供 bug report。
```

### 27.7 看 patch validation 和 reflection

你要找的信息：

- 是否生成了 patch candidate。
- patch 是否能 apply。
- 目标测试是否通过。
- regression guard 是否通过。
- 是否有 reflection round。
- refined candidate 是否成功。

你要能解释：

```text
patch validation 是修复是否成立的最终证据。
reflection 说明系统能利用失败反馈继续尝试，而不是单轮结束。
```

例子：

```text
initial patch failed
reflection depth = 1
successful candidates = 1
```

含义：

```text
初始修复不成功，但 Agent 使用失败反馈生成下一轮补丁，并通过目标测试。
```

## 28. 面试官追问时的深度答法

### 28.1 追问：你的系统和 SWE-agent、OpenHands 这类项目有什么区别

可以回答：

> 我这个项目更偏算法和可审计分析，不是做通用软件工程自动化。重点在 Python 仓库的结构建模、函数级 Top-k 缺陷定位、测试证据融合和受控 patch validation。通用 Agent 更强调 shell/browser 工具使用和长程任务执行，我的项目更强调程序分析、图建模、缺陷信号和修复闭环。

继续补充：

> 所以它不是要替代所有通用编码 Agent，而是展示一个垂直领域 Agent：输入仓库后，围绕代码理解、缺陷定位、补丁验证形成闭环。

### 28.2 追问：为什么不用纯 RAG

可以回答：

> RAG 更适合检索文档或代码片段，但缺陷定位需要结构化程序信息。比如函数调用关系、测试失败路径、source role 和静态 bug signal 不是简单向量检索能稳定表达的。所以我先做 AST 和 Program Graph，再把检索或 LLM 推理放在结构化证据之上。

### 28.3 追问：为什么不用纯 LLM 读完整仓库

可以回答：

> 纯 LLM 读完整仓库有上下文长度、成本、幻觉和不可审计问题。我的做法是先用确定性程序分析压缩仓库，把问题从“读全仓库”变成“分析 Top-k 函数和相关证据”。这样 LLM 的输入更聚焦，输出也更容易验证。

### 28.4 追问：你的 Top-k 分数是否是机器学习模型

可以回答：

> 当前更像可解释的 scoring/ranking 系统，不是训练出来的黑盒模型。它融合静态规则、图结构、动态证据和 source role。这样做的优点是可解释、样本需求低、适合项目展示。后续可以扩展为学习型 ranker，比如用 benchmark case 学习不同 signal 的权重。

### 28.5 追问：如果静态信号误报怎么办

可以回答：

> 静态信号只是 feature，不是最终结论。它会影响 Top-k，但不会直接触发修复成功。真正采纳 patch 前必须有测试或 sandbox validation。如果没有动态证据，系统会报告 evidence insufficient。

### 28.6 追问：如果测试环境跑不起来怎么办

可以回答：

> Agent 会把它分类为 environment blocker，而不是当成代码 bug。报告会记录缺少依赖、命令不可用、超时或 runner 不可复现等原因，并给出下一步动作，例如准备依赖、限制测试范围、提供 failing test 或只输出静态定位报告。

### 28.7 追问：LLM 调用失败怎么办

可以回答：

> 系统有 rule fallback。LLM 失败会被记录成可审计 blocker 或 fallback 事件，不会导致整个 Agent 失控。比如 planner 可以回退到规则 Controller，patch generation 可以回退到 rule candidates。

### 28.8 追问：为什么 memory 算 Agent 特征

可以回答：

> 因为代码修复不是单轮任务。Memory 保存用户约束、历史失败 patch、测试摘要和 repair strategy，让下一轮行动受历史影响。比如用户说“不要修改公共 API”，后续 patch context 会带上这个约束；如果某个 patch 已失败，下一轮会避免重复生成同类补丁。

## 29. 初学者常见误区

### 29.1 误区一：以为用了 LLM 就是 Agent

纠正：

```text
LLM 是能力组件，不等于 Agent。
Agent 要有状态、目标、动作、反馈和重规划。
```

本项目中 Agent 特征来自 Controller 闭环、action registry、验证反馈、reflection 和 memory。

### 29.2 误区二：以为测试通过就说明可以修复

纠正：

```text
测试通过只能说明当前测试集没有暴露失败。
它不能告诉系统 bug 在哪里。
```

所以 passing tests 是 regression guard，不是 repair oracle。

### 29.3 误区三：以为 blocker 是失败

纠正：

```text
在真实工程中，正确识别 blocker 是能力。
```

例如非 Python 仓库、无测试命令、依赖不可复现、缺少 failing evidence，都应该被明确报告，而不是伪装成功。

### 29.4 误区四：以为 Top-k 就等于 bug 结论

纠正：

```text
Top-k 是可疑度排序。
它用于缩小搜索空间，不是最终证明。
```

最终是否修复仍要看 patch validation 和测试结果。

### 29.5 误区五：以为 reflection 是让模型再想一次

纠正：

```text
有效 reflection 必须基于执行反馈。
```

本项目中的 reflection 会读取失败 patch、测试输出、错误类型和历史记忆，而不是空泛地让模型“重新思考”。

### 29.6 误区六：以为安全门控会削弱 Agent

纠正：

```text
安全门控不是限制智能，而是让智能可落地。
```

代码修复 Agent 涉及文件修改和命令执行，必须有 action 白名单、风险策略和 sandbox 验证。

## 30. 建议你亲自做的实验

### 30.1 实验一：对可测试仓库运行 Agent

目标仓库：

```text
pytest-dev/pluggy
```

你要观察：

- 是否识别 `src/` layout。
- 是否发现 pytest。
- 测试通过后 Agent 做什么。
- 是否没有强行生成 patch。

学习收获：

```text
理解 passing tests 为什么是 regression guard。
```

### 30.2 实验二：对 blocker 仓库运行 Agent

目标仓库：

```text
octocat/Hello-World
```

你要观察：

- 是否发现没有 Python 源码。
- blocker 是什么。
- selected action 是什么。

学习收获：

```text
理解 Agent 如何正确停止，而不是虚构分析结果。
```

### 30.3 实验三：对 sampleproject 运行 LLM Planner

目标仓库：

```text
pypa/sampleproject
```

你要观察：

- LLM 是否给出规划建议。
- 建议是否通过安全门。
- 如果被拒绝，拒绝原因是什么。
- Controller fallback 到什么动作。

学习收获：

```text
理解 LLM Planner 和安全门控如何协作。
```

### 30.4 实验四：学习 TheAlgorithms 修复样例

目标：

```text
阅读 TheAlgorithms/Python gronsfeld repair reflection 样例报告。
```

你要观察：

- Top-1 为什么是 `gronsfeld`。
- 初始 patch 为什么失败。
- reflection 如何生成下一轮。
- sandbox 如何验证成功。

学习收获：

```text
理解自动修复闭环：定位 -> 生成 -> 验证 -> 失败反思 -> 重试。
```

### 30.5 实验五：多轮对话

你可以依次输入：

```text
解释上一次失败原因
不要修改公共 API
继续修复 Top-1 函数
重新运行 pytest
```

你要观察：

- 用户约束是否被记录。
- Agent 是否复用 session。
- 是否输出下一步动作或命令。

学习收获：

```text
理解 chat-ui 不是自由闲聊，而是围绕 repo session 的任务型多轮 Agent。
```

## 31. 深挖一：Program Graph 与 GraphScore 如何抗追问

这一章解决的是面试官继续追问时最常见的问题：

```text
你说用了 Program Graph，那这个图到底有哪些节点、哪些边？
GraphScore 的每个子信号到底从哪里来？
为什么它不是简单的调用图 degree？
```

{{DIAGRAM:program_graph_deep|Program Graph 节点、边与 GraphScore 证据来源}}

### 31.1 Program Graph 的构建入口

Program Graph 的输入不是原始字符串，而是前面几步已经结构化后的结果：

- `RepoParseResult`：包含文件、函数、类、import、call site。
- `CallGraph`：包含函数间调用边。
- AST/CFG 分析结果：用于变量流、控制流和基本块。

构建流程可以概括为：

```text
RepoParseResult
  -> file/class/function/import nodes
CallGraph
  -> calls / awaits / module_depends_on edges
Parsed tests
  -> tested_by edges
AST variable analysis
  -> defines / uses / data_depends_on / key_flows_to_subscript
Cross-function call args
  -> arg_flows_to_param / return_flows_to_var
Control-flow analysis
  -> controls / cfg_entry / cfg_next / cfg_branch / cfg_loop / cfg_exception
```

这意味着 Program Graph 不是单一图，而是一个多关系异构图。

### 31.2 Program Graph 的节点类型

| 节点类型 | 来源 | 作用 |
| --- | --- | --- |
| `file` | 仓库文件路径 | 表示源码文件，用于 `contains` 和 import 关系 |
| `class` | AST class definition | 表示类结构 |
| `function` | AST function / async function | Top-k 缺陷定位的核心候选节点 |
| `import` | import/from import 语句 | 表示模块依赖入口 |
| `variable` | 函数内变量定义/使用 | 支持数据依赖和 key-flow 分析 |
| `statement` | if/for/while/try 等控制语句 | 支持控制流风险建模 |
| `basic_block` | CFG 基本块 | 支持更细的控制流路径分析 |

面试时要强调：最终排名的是 `function` 节点，但其他节点给函数提供证据。

### 31.3 Program Graph 的边类型

| 边类型 | 由什么生成 | 解释 |
| --- | --- | --- |
| `contains` | 文件/类/函数/语句/基本块的包含关系 | 说明结构层级 |
| `imports` | import 语句 | 说明文件依赖哪些模块 |
| `calls` | CallGraph 边 | 表示函数调用函数 |
| `awaits` | await/task/gather 等异步调用 | 表示异步调用关系 |
| `module_depends_on` | 跨文件函数调用 | 表示模块间依赖 |
| `tested_by` | 测试函数调用生产函数 | 表示测试覆盖目标 |
| `defines` | 变量定义事件 | 表示函数定义了某变量 |
| `uses` | 变量使用事件 | 表示函数使用了某变量 |
| `data_depends_on` | 变量赋值/表达式依赖 | 表示一个变量的数据依赖另一个变量 |
| `key_flows_to_subscript` | 字典 key 到下标访问 | 支持 dict missing key 类规则 |
| `arg_flows_to_param` | 调用实参到被调函数形参 | 表示跨函数参数流 |
| `return_flows_to_var` | 被调函数返回到调用方变量 | 表示跨函数返回值流 |
| `controls` | 控制语句控制函数内部逻辑 | 表示 if/for/while/try 对函数行为的影响 |
| `cfg_entry` | 函数到 CFG 入口块 | 表示控制流起点 |
| `cfg_next` | 基本块顺序边 | 表示顺序执行 |
| `cfg_branch` | 条件分支边 | 表示 if/else 分支 |
| `cfg_loop` | 循环边 | 表示循环结构 |
| `cfg_exception` | 异常边 | 表示 try/except/finally 结构 |

### 31.4 GraphScore 不只是调用次数

GraphScore 不是简单的：

```text
GraphScore = degree(function)
```

它实际融合了：

- 失败测试是否覆盖该函数。
- traceback 是否命中该函数。
- 函数距离失败测试有多近。
- 函数是否处在重要调用位置。
- 函数是否处在跨模块依赖链上。
- 函数是否有数据流或控制流复杂性。
- 函数是否处在异步调用链上。
- 函数是否有静态风险信号。
- 修改该函数的 patch 风险是否过高。

所以面试时可以说：

> GraphScore 是一个图结构证据融合分，而不是简单的中心性分。它把测试覆盖、traceback、最短路径、PageRank、caller impact、module dependency、data/control flow 和 patch risk 放在一个可解释公式里。

### 31.5 proximity 如何计算

`proximity` 表示候选函数离失败测试有多近。

计算思想：

```text
distance = ProgramGraph.shortest_path_distance(test_id, function_id, edge_types={"calls", "tested_by"})
proximity = 1 / (1 + distance)
```

例子：

```text
失败测试 test_empty_key 直接 tested_by/calls 到 gronsfeld：
  distance = 1
  proximity = 1 / (1 + 1) = 0.5

如果中间隔了一个 wrapper：
  distance = 2
  proximity = 1 / (1 + 2) = 0.3333
```

直觉是：离失败测试越近，越可疑；但不是只有直接命中才可疑。

### 31.6 caller_impact 为什么只看生产调用者

`caller_impact` 会追踪生产代码里的传递调用者，默认最多向上看有限深度。它会给不同情况加权：

- 距离越远，影响衰减。
- 跨文件调用有额外加权。
- await 异步路径有额外加权。
- 测试函数不会作为生产调用者放大影响。

为什么不把测试调用者也算进去：

```text
测试代码本来就是证据来源。
如果把测试调用者也作为生产影响放大，会让测试 helper 过度影响 Top-k。
```

面试表达：

> caller_impact 是为了估计生产代码影响面，而不是重复计算测试覆盖。因此它会排除测试函数，只看生产调用链。

### 31.7 module_dependency 有什么用

`module_dependency` 来自跨文件调用边 `module_depends_on`。它会考虑：

- 函数是否被其他文件调用。
- 调用是否跨模块。
- 包路径距离。
- 是否是相对 import。

它的作用是判断一个函数是否处在模块边界上。模块边界函数通常有更强的影响范围，也更可能是公共行为变化的入口。

### 31.8 data_dependency 与 key_flow 为什么重要

数据依赖用于回答：

```text
这个变量从哪里来，又流向哪里？
```

例如：

```text
key = request.args["name"]
value = config[key]
```

图中可能出现：

```text
key variable -> config subscript
```

这对 dict missing key、None guard、boundary value 等 bug 很重要，因为 bug 往往不是调用关系导致，而是数据值沿路径传播导致。

### 31.9 patch_risk 为什么会扣分

`patch_risk` 主要来自调用入度：

```text
patch_risk = in_degree(function, calls) / max_in_degree
```

被很多地方调用的函数，修改影响面更大。系统会把它作为扣分项，避免在证据相近时优先修改高风险核心函数。

注意这不是说高风险函数不能修。它只是让排序更加保守：

```text
如果一个函数证据非常强，即使 risk 高也可能排第一。
如果两个函数证据相近，风险低的函数更适合优先尝试。
```

### 31.10 面试官追问时怎么回答

问题：你的 Program Graph 和普通 Call Graph 有什么区别？

回答：

> Call Graph 只有函数调用边，而 Program Graph 是多关系图。它包含文件、函数、类、import、变量、控制语句、基本块等节点，也包含 calls、tested_by、data_depends_on、arg_flows_to_param、return_flows_to_var、controls、cfg_branch、module_depends_on 等边。因此它不仅能表达“谁调用谁”，还能表达测试证据、数据流、控制流和模块依赖。

问题：GraphScore 为什么可信？

回答：

> 它不是黑盒模型，而是可解释的图信号融合。每个子项都能追溯到具体图边或测试证据，例如 traceback_hit、test_coverage、proximity、pagerank、caller_impact、data_dependency 和 patch_risk。最终报告会保留 signals，因此可以解释每个函数为什么排在前面。

问题：GraphScore 有没有可能误导？

回答：

> 有可能。比如中心函数被很多地方调用，centrality 高但不一定是 bug。因此系统加入 patch_risk 扣分，并且 FinalScore 还融合 SBFL、Static、Semantic、LLM 和 sandbox 验证，避免单一图信号决定最终结论。

## 32. 深挖二：Patch 生成、安全门控、Sandbox 与 Reflection

这一章解决的是面试官继续追问时最常见的问题：

```text
LLM 生成 patch 后怎么保证不乱改？
怎么知道 patch 真修好了？
reflection 到底是不是只让模型再想一次？
```

{{DIAGRAM:patch_repair_deep|Patch 生成、Safety Gate、Sandbox 与 Reflection 闭环}}

### 32.1 Patch 生成从哪里开始

Patch 生成不是从全仓库开始，而是从 Top-k 定位结果开始。

输入包括：

- Top-k suspicious functions。
- 目标函数源码。
- 函数文件路径和行号。
- FinalScore 和 signals。
- 静态 findings。
- failing test / dynamic oracle。
- 推荐验证命令或 pytest nodeid。
- 用户约束。
- session patch memory。

这一步的核心思想：

```text
先定位，再生成。
不要让 LLM 在全仓库里盲改。
```

### 32.2 三种 Patch 生成模式

| 模式 | 生成来源 | 优点 | 风险 |
| --- | --- | --- | --- |
| `rule` | 静态规则模板 | 稳定、可解释、低成本 | 覆盖范围有限 |
| `llm` | 大模型生成 fixed_source | 泛化能力强 | 可能格式错误、越界修改 |
| `hybrid` | 规则 + LLM | 兼顾稳定性和泛化 | 需要区分 rule 成功和 LLM 成功 |

面试时要注意：不要说所有 patch 都是 LLM 生成的。当前系统是多生成器结构，LLM 是可选增强。

### 32.3 LLM patch prompt 里包含什么

LLM 不是只收到一句“修复这个函数”。它会收到结构化 payload，包含：

- task：返回 minimal corrected fixed_source。
- constraints：只返回 JSON、不写 markdown、不删除测试、保持函数名和公共签名。
- top_k_suspicious_functions：排名、函数名、文件、分数、原因。
- function：目标函数 id、name、file_path、start/end line、source。
- localization：score、rank、signals、reason、findings。
- failing_test_nodeids：失败测试节点。
- failure_evidence：失败信息和动态 oracle。
- public_api_evidence：公共 API 调用证据。
- call_graph_context：调用上下文。
- previous_failed_patch_fingerprints：历史失败 patch 指纹。
- required_schema：要求输出 `fixed_source` 或 `fixed_sources`。

核心约束包括：

```text
Return only JSON.
Do not include markdown.
Keep the same function name and public signature unless required.
Do not remove tests.
Prefer the smallest behavior-preserving patch.
Only patch this function because it is within the top-k suspicious functions.
```

### 32.4 Safety Gate 检查什么

Patch 候选生成后，不会直接进 sandbox，而是先过函数级安全门。

Safety Gate 使用 `validate_function_patch(old_source, new_source)` 检查：

| 检查项 | 含义 | 失败原因示例 |
| --- | --- | --- |
| AST 可解析 | 新旧函数都必须是合法 Python AST | `invalid_python_ast` |
| 单函数范围 | 新旧源码都必须只有一个顶层函数，且函数名不变 | `scope_not_limited_to_original_function` |
| 缩进一致 | 保持原函数缩进层级 | scope 相关失败 |
| 签名保护 | 默认不能改参数、返回注解、type comment | `signature_changed` |
| 装饰器保护 | 默认不能改 decorator | `decorator_changed` |
| patch 大小 | changed lines 不超过阈值 | `patch_too_large` |
| 修改比例 | changed_lines / old_line_count 不超过阈值 | `patch_change_ratio_too_large` |

默认阈值：

```text
max_changed_lines = 80
max_line_change_ratio = 3.0
```

只有少数规则允许签名变化，例如 `mutable_default_arg` 这类规则可能需要改默认参数。

### 32.5 为什么 Safety Gate 在 Sandbox 之前

原因是 sandbox 只验证“执行后测试是否过”，但不能完整约束 patch 的工程边界。

例如一个 patch 可能：

- 修改整个函数外的代码。
- 改函数签名导致公共 API 破坏。
- 改 decorator。
- 把测试删掉。
- 大面积重写逻辑。

这些在某个窄测试里可能不一定暴露出来，所以需要 pre-sandbox safety gate。

面试表达：

> Safety Gate 解决的是“能不能执行之前，这个 patch 是否符合修改边界”；Sandbox 解决的是“执行之后，这个 patch 是否真的通过测试”。两者不是替代关系。

### 32.6 Sandbox 具体验证什么

Sandbox 的流程：

```text
创建临时目录
复制原仓库到 sandbox repo
应用 patch candidate
运行 pytest bootstrap
收集 returncode/stdout/stderr/traceback/passed/failed/timeout
删除临时目录
```

它不会直接污染原仓库，因为每次都在临时复制目录里执行。

默认测试命令结构类似：

```text
python pytest_bootstrap.py <sandbox_repo> -q <test_args>
```

如果 patch 无法应用，会返回 patch apply error；如果 pytest 超时，会返回 timeout；如果测试失败，会记录失败数和 traceback。

### 32.7 Sandbox 成功不等于全项目完美

要区分三种成功：

| 状态 | 含义 |
| --- | --- |
| target test pass | 目标失败测试通过 |
| regression guard pass | 回归测试通过 |
| full project green | 全项目测试全绿 |

很多真实仓库无法轻易跑完整测试，所以报告里必须写清楚验证范围。

面试时不要说：

```text
patch 通过了，所以整个项目一定没问题。
```

应该说：

```text
patch 在 sandbox 中通过了目标 pytest；如果全量回归存在历史失败或环境 blocker，报告会保留 caveat。
```

### 32.8 Reflection 如何工作

Reflection 不是一句“让模型再想想”。它有执行反馈输入：

- stdout。
- stderr。
- traceback。
- return code。
- passed/failed count。
- timeout。
- previous patch diff。
- previous fixed source。
- failed source fingerprint。
- judge feedback。
- cross-file context。

失败类型会被分类：

| 错误类型 | 反思策略 |
| --- | --- |
| `SyntaxError` | 生成更严格语法约束的 patch |
| `AssertionError` | 对比 expected/actual，修正逻辑 |
| `TimeoutError` | 检查循环边界和递归终止 |
| `ImportError` | 判断依赖或导入路径问题 |
| `AttributeError` | 检查 None/类型/对象属性 |
| `TypeError` | 检查参数类型和边界输入 |
| `TestFailure` | 用测试输出继续修复 |
| `Unknown` | 保守重试或输出 blocker |

### 32.9 如何避免重复失败 patch

系统会记录 patch 指纹：

- diff fingerprint。
- fixed_source fingerprint。
- failed source fingerprints。
- session patch memory 中的 avoid list。

LLM reflection prompt 会明确要求：

```text
Do not return a fixed_source whose normalized fingerprint appears in failed_patch_memory.
```

这就是 memory 在自动修复中的具体作用：避免每一轮都生成同样失败的 patch。

### 32.10 RepairLoop 如何选择下一轮

RepairLoop 会先对候选 patch 排序、去重，再依次验证：

```text
rank patch candidates
dedupe candidates
annotate refinement context
for round_index < max_rounds:
    run safety gate
    if safe: apply patch and test in sandbox
    score patch
    reflect failure
    if success: stop
    if should_retry: insert refined candidates
```

它不是无限循环。默认有 `max_rounds`，并且会根据 `should_retry` 决定是否继续。

### 32.11 面试官追问时怎么回答

问题：怎么防止 LLM 乱改？

回答：

> 第一，LLM 只拿 Top-k 目标函数上下文，不拿全仓库自由改。第二，prompt 明确要求只返回 fixed_source、保持函数名和公共签名、不删除测试。第三，候选 patch 进入 safety gate，检查 AST、scope、signature、decorator 和 diff 大小。第四，只有通过 safety gate 的 patch 才进入 sandbox。

问题：怎么证明 patch 真有效？

回答：

> 成功标准不是 LLM 自评，而是 sandbox 执行结果。系统复制仓库到临时目录，应用 patch，运行目标 pytest 或推荐验证命令，并记录 return code、stdout/stderr、traceback、passed/failed/timeout。报告里会区分 target test pass、regression guard 和 full project green。

问题：Reflection 有什么实际价值？

回答：

> Reflection 使用真实执行反馈，包括 stdout/stderr/traceback、错误类型和历史失败 patch 指纹。它会根据 SyntaxError、AssertionError、TimeoutError 等失败类型选择修复策略，并避免生成历史失败指纹相同的 fixed_source。

## 33. 深挖三：AgentController 状态机、Action Registry 与 LLM Planner

这一章解决的是面试官继续追问时最常见的问题：

```text
AgentController 到底怎么决定下一步？
LLM Planner 和规则 Controller 冲突时听谁的？
action registry 是做什么的？
```

{{DIAGRAM:controller_state|AgentController Stage/Blocker 到 Action 的状态机}}

### 33.1 Controller 的输入是什么

Controller 的输入不是一句自然语言，而是结构化 summary：

- `current_stage`：当前阶段。
- `blocker`：当前阻塞原因。
- `dynamic_evidence_level`：动态证据等级。
- `fault`：Top-k 定位结果。
- `repository_test_*`：测试计划、执行、环境诊断。
- `patch_validation_*`：补丁验证状态。
- `reflection_summary`：反思结果。
- `agent_memory_report`：session/repo/repair memory。
- `agent_invocation`：运行模式、profile、自动动作预算。

Controller 的工作是把这些状态转成下一步 action。

### 33.2 Controller 的六步闭环具体产物

| 阶段 | 输入 | 输出 |
| --- | --- | --- |
| Observe | repo/readiness/fault/test/patch/memory | 观察摘要 |
| Plan | 当前 stage 和 blocker | selected action |
| Act | action spec 和命令 | 可执行命令或 blocker 指令 |
| Verify | 预期 artifact 和成功条件 | verify plan |
| Reflect | action 失败或 blocker | failure hypothesis 和 fallback action |
| Replan | verify/reflection/blocker | next policy 和下一轮动作 |

### 33.3 Action Registry 有什么用

Action Registry 是动作白名单。它记录每个 action：

- action_id。
- phase。
- tool。
- module。
- input_requirements。
- expected_artifact。
- success_condition。
- failure_condition。
- blocker_type。
- retry_policy。
- next_possible_actions。
- aliases。

例如：

| action | 作用 |
| --- | --- |
| `clone_or_load_repository` | 拉取或加载仓库 |
| `discover_repository_structure` | 发现仓库结构 |
| `discover_tests` | 找测试入口 |
| `diagnose_environment` | 诊断环境 blocker |
| `run_repository_tests` | 运行仓库测试 |
| `localize_fault` | 构建 Top-k 定位 |
| `generate_llm_patch_candidates` | 生成 LLM patch |
| `generate_hybrid_patch_candidates` | 生成 rule+LLM patch |
| `validate_patch_in_sandbox` | sandbox 验证 |
| `run_llm_patch_reflection_loop` | LLM reflection |
| `run_llm_patch_judge` | LLM judge |
| `emit_blocker_report` | 输出 blocker |
| `generate_final_agent_report` | 输出最终报告 |

### 33.4 为什么需要 action alias

用户、规则和 LLM 可能说出不同名字，例如：

```text
build_static_graph_fault_ranking
mine_static_bug_signals
run_dynamic_fault_localization
```

这些可能都属于更 canonical 的：

```text
localize_fault
```

Alias 的作用是把不同表述归一化，避免 LLM 或规则用不同名字导致系统误判。

### 33.5 Stage/Blocker 如何决定 action

Controller 的 `_select_action` 根据 `current_stage` 和 blocker 做分支选择。

典型映射：

| 当前状态 | 条件 | 选择动作 |
| --- | --- | --- |
| `source_import_blocked` | GitHub fetch 失败 | `retry_with_github_token_or_cache` |
| `source_import_blocked` | 没有可分析 Python 源码 | `adjust_source_filters` |
| `phase1_repo_understanding` | 有结构但无静态候选 | `expand_static_candidate_search` |
| `phase2_static_bug_signal_mining` | 静态候选已有但图定位未完成 | `build_static_graph_fault_ranking` |
| `phase2_static_graph_fault_localization` | 静态定位 ready 但没动态证据 | `run_repository_tests_with_checkout` |
| `phase2_static_graph_fault_localization` | passing tests | `convert_passing_tests_to_regression_guard` 或等待 failing evidence |
| `phase2_static_graph_fault_localization` | 环境缺失 | `prepare_repository_test_environment` |
| `phase2_dynamic_fault_localization` | 有动态证据 | `generate_and_validate_patches` |
| `phase3_patch_validation` | patch 失败且没 reflection | `run_patch_reflection_loop` |
| `phase3_patch_validation` | repair ready | `run_search_and_ablation_evaluation` 或最终报告 |

### 33.6 LLM Planner 什么时候启用

LLM Planner/Replanner 在以下情况下可能启用：

- 显式设置 `CIA_LLM_REPLAN_ENABLED`。
- `agent-auto` profile。
- 启用 agent mode。
- 启用 auto controller actions。
- 外部注入 replan client。

LLM Planner 的输入包括：

- repo。
- current_stage。
- blocker。
- fault localization。
- selected_action。
- verification。
- reflection。
- rule_replan。
- termination。
- memory_context。
- observations。

它必须返回 JSON，包含：

- selected_action / recommended_action。
- reason。
- confidence。
- risk。
- blocker。
- required_evidence。
- next_plan。
- memory_used。
- should_override_controller。

### 33.7 LLM Planner 和规则 Controller 冲突听谁的

当前设计是：

```text
LLM Planner = advisory only
Controller + safety gate = final authority
```

LLM 的建议会经过安全门：

| 情况 | 结果 |
| --- | --- |
| LLM 没给 action | `not_requested` |
| LLM 推荐未注册 action | `blocked` |
| LLM 推荐 action 和 Controller 匹配 | `pass` |
| LLM 推荐已注册但和 Controller 不一致 | `advisory_only`，Controller 保留规则动作 |
| LLM 不可用或 API key 缺失 | rule fallback |

这就是为什么之前 `llm_recommended_action_not_registered` 会被拒绝。

### 33.8 Memory 如何进入 Planner

LLM Planner 的 memory context 包含：

- session id。
- turn count。
- last intent。
- active scope。
- repo。
- test command。
- test status。
- top suspicious functions。
- failed patch count。
- patch attempt count。
- failed patch fingerprints。
- latest failure category。
- user constraints。
- repair strategy preferences。

LLM 可以使用这些信息来建议下一步，但不能绕过 action registry。

### 33.9 Auto Controller 为什么要有 max actions

自动动作预算用于防止 Agent 无限循环。真实仓库中可能出现：

- 环境一直不可复现。
- LLM provider 一直失败。
- patch 一直过不了测试。
- 测试一直 timeout。

所以 Controller 需要限制自动动作数，并在达到边界时输出 blocker 和 next action，而不是无限执行。

### 33.10 面试官追问时怎么回答

问题：你的 Agent 是不是还是规则工作流？

回答：

> 它不是纯固定 workflow，因为 Controller 会根据 current_stage、blocker、dynamic evidence、patch validation 和 memory 选择不同 action。但它也不是无边界自主 Agent。它是受控 Agent：规则 Controller 负责安全边界，LLM Planner 做 advisory reasoning，sandbox 负责事实验证。

问题：LLM Planner 为什么不能直接 override？

回答：

> 因为代码修复涉及执行命令和修改文件，必须有 action registry 和 safety gate。LLM 可以建议，但如果 action 未注册或和当前 blocker 不匹配，就只能作为 advisory 记录。最终执行权在 Controller 和 sandbox gate。

问题：Action Registry 的意义是什么？

回答：

> 它把 Agent 能做的事情白名单化，并为每个 action 定义输入、产物、成功条件、失败条件和 retry policy。这让 Agent 行为可审计，也让 LLM 推荐不会变成任意命令执行。

## 34. 深挖四：评估指标、Ablation 与 Weight Search

这一章解决的是算法向面试最容易被追问的问题：

```text
你这个权重怎么来的？
怎么证明 Top-k 定位有效？
有没有 ablation？
有没有校准 FinalScore 的置信度？
```

{{DIAGRAM:evaluation_metrics|评估指标、权重搜索、Ablation 与置信度校准}}

### 34.1 权重是不是训练出来的

当前主权重更准确地说是：

```text
可解释启发式权重 + weight_search/ablation/evaluation 支撑
```

不能说成：

```text
这些权重是大规模训练出来的最优参数。
```

正确表达：

> 当前默认权重是根据程序分析经验设置的可解释启发式权重；项目同时提供 weight_search、ablation、benchmark metrics 和 calibration 模块，用于验证权重组合对 Top-k/MRR/MAP/EXAM 等指标的影响，并支持后续系统化调参。

### 34.2 Top-k Accuracy

Top-k Accuracy 衡量：

```text
真实 buggy function 是否出现在前 k 个候选里。
```

公式：

```text
TopKAccuracy@k =
  命中 ground truth 的 case 数 / 总 case 数
```

例子：

```text
10 个 benchmark case 中，有 8 个 case 的真实 bug 出现在 Top-3：
Top3Accuracy = 8 / 10 = 0.8
```

解释：

```text
Top-k 越高，说明定位器越能把真实 bug 放进前排候选。
```

### 34.3 MRR

MRR 全称 Mean Reciprocal Rank，关注真实 bug 第一次出现的位置。

单个 case：

```text
ReciprocalRank = 1 / first_relevant_rank
```

如果真实 bug 排第 1：

```text
RR = 1 / 1 = 1.0
```

如果真实 bug 排第 4：

```text
RR = 1 / 4 = 0.25
```

多个 case 取平均就是 MRR。

解释：

```text
MRR 对 Top-1 很敏感，越早把真实 bug 排出来越好。
```

### 34.4 MAP

MAP 全称 Mean Average Precision。它适合一个 case 可能有多个 buggy functions 的情况。

单个 case 的 Average Precision：

```text
每次命中 ground truth 时，计算当前位置的 precision，
然后对所有 ground truth 命中取平均。
```

例子：

```text
ground truth = {A, C}
ranked = [A, B, C, D]

命中 A 时 index=1，precision=1/1=1.0
命中 C 时 index=3，precision=2/3=0.6667
AP = (1.0 + 0.6667) / 2 = 0.8333
```

多个 case 取平均就是 MAP。

### 34.5 NDCG@3

NDCG@3 衡量前 3 名排序质量。它考虑命中位置折损：

```text
越靠前的命中贡献越大。
```

直觉解释：

```text
真实 bug 在第 1 名比第 3 名更有价值。
NDCG 用 log 折损来表达这种差异。
```

### 34.6 EXAM Score

EXAM Score 来自缺陷定位领域，表示为了找到第一个真实 bug，需要检查多少比例的候选。

公式直觉：

```text
EXAM = (first_relevant_rank - 1) / candidate_count
```

如果真实 bug 排第 1：

```text
EXAM = 0
```

如果 100 个候选里真实 bug 排第 20：

```text
EXAM = 19 / 100 = 0.19
```

注意：EXAM 越低越好。

### 34.7 Validation Score 如何组合

Weight Search 中会把多个指标组合成 validation score：

```text
ValidationScore =
    0.25 * MAP
  + 0.25 * MRR
  + 0.20 * NDCG@3
  + 0.15 * Top1
  + 0.10 * Top3
  + 0.05 * (1 - EXAM)
```

为什么这样组合：

- MAP 关注多 ground truth 的整体排序质量。
- MRR 关注第一个正确结果出现得多早。
- NDCG@3 关注前 3 名排序质量。
- Top1 关注最直接的第一名命中。
- Top3 关注修复候选集是否覆盖真实 bug。
- `1 - EXAM` 把“越低越好”的 EXAM 转成“越高越好”。

### 34.8 Robust Validation Score

如果只在整体数据上得分高，可能是某一类 case 特别容易。为了看泛化，系统会做 source group / holdout 分析。

robust score 会惩罚：

- Top1 在 holdout group 上掉太多。
- MAP 在 holdout group 上掉太多。

直觉：

```text
不只要平均分高，还要不同来源仓库上不要大幅退化。
```

### 34.9 Brier Score 和置信度校准

FinalScore 不只是排序分，也常被当作置信度参考。因此需要检查：

```text
分数 0.8 的 case，真的大约有 80% Top-1 命中吗？
```

Brier Score 公式：

```text
Brier = mean((confidence - label)^2)
```

其中：

- `confidence` 是 Top-1 的 FinalScore。
- `label = 1` 表示 Top-1 命中 ground truth。
- `label = 0` 表示 Top-1 没命中。

Brier 越低越好。

ECE，全称 Expected Calibration Error，用于衡量不同置信度分桶中的预测置信度和真实命中率是否接近。

### 34.10 Ablation Study 做什么

Ablation 的目的：

```text
去掉某个模块，看指标是否下降。
```

当前项目中 localization ablation 包括：

| Variant | 去掉什么 | 验证什么 |
| --- | --- | --- |
| `without_static_rules` | 静态规则 | 静态规则是否有贡献 |
| `without_test_signals` | 测试信号 | 动态证据是否有贡献 |
| `without_line_coverage` | 行覆盖 | 行覆盖是否有贡献 |
| `without_branch_coverage` | 分支覆盖 | 分支信号是否有贡献 |
| `without_path_coverage` | 路径覆盖 | 路径信号是否有贡献 |
| `without_data_dependency` | 数据依赖 | 数据流图是否有贡献 |
| `without_control_flow` | 控制流 | CFG/控制流是否有贡献 |
| `without_pagerank` | PageRank | 图重要性是否有贡献 |
| `without_caller_impact` | 调用影响 | 生产调用链是否有贡献 |
| `without_module_dependency` | 模块依赖 | 跨模块依赖是否有贡献 |
| `without_async_call_graph` | 异步调用图 | async/await 信号是否有贡献 |
| `without_semantic_similarity` | 语义相似度 | token overlap 是否有贡献 |
| `without_llm_score` | LLM 分数 | LLM scorer 是否有贡献 |

搜索/修复相关 ablation 包括：

- `without_reflection`
- `without_beam_search`
- `without_patch_prior`
- `without_diversity_reranking`
- `without_candidate_deduplication`
- `without_multi_patch_repair`
- `without_graph_bundle_search`

### 34.11 面试官追问时怎么回答

问题：权重是不是拍脑袋？

回答：

> 默认权重是可解释启发式，不应该说成训练最优。但项目里有 weight_search 和 ablation，用 Top1、Top3、MRR、MAP、NDCG@3、EXAM、robust validation score 去评估不同组合。也就是说，当前权重有工程可解释性，后续可以通过 benchmark 做系统化调参。

问题：怎么证明 Program Graph 有用？

回答：

> 用 ablation。比如 `without_data_dependency`、`without_control_flow`、`without_pagerank`、`without_caller_impact`、`without_module_dependency` 分别去掉图组件，看 Top-k/MRR/MAP 是否下降。如果下降，说明对应图信号对定位有贡献。

问题：怎么证明 LLMScore 有用？

回答：

> 对比 full 和 `without_llm_score`。如果 full 在 MRR/MAP 或 Top1 上更好，说明 LLMScore 有贡献；如果没有提升，说明当前 case 主要依赖静态/动态图证据。无论如何 LLMScore 只是排序信号，不替代 sandbox。

问题：为什么要做 calibration？

回答：

> 排名指标只能说明排序好不好，但不能说明 FinalScore 能不能当置信度。Brier Score 和 ECE 检查的是分数校准，比如 0.8 分是否真的接近 80% 命中率。这对报告可信度很重要。

## 35. 最终复习 Checklist

面试前你应该能回答：

- 这个项目解决什么问题？
- 为什么不能直接把仓库交给 LLM？
- Repo Understanding 做了什么？
- AST 提取了哪些信息？
- Call Graph 有什么用？
- Program Graph 为什么体现算法深度？
- 静态信号和真实 bug 的关系是什么？
- Top-k 分数由哪些证据组成？
- passing tests、failing tests、environment blocker 有什么区别？
- AgentController 的六步闭环分别是什么？
- LLM Planner 如何接入？
- 为什么需要 action registry？
- sandbox 如何验证补丁？
- reflection 如何避免一次失败就停止？
- memory 系统保存什么？
- 多轮对话适合什么场景？
- 当前项目边界是什么？
- 简历上应该如何准确表述？

如果这些问题都能讲清楚，这个项目就可以作为算法向 Agent 项目写进简历。
