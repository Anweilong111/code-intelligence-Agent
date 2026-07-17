# Code Intelligence Agent V3：10 分钟现场演示指南

## 1. 演示目标

10 分钟演示要证明四件事：

1. 这是会观察、选择动作、执行、验证、反思和重规划的受控 Agent；
2. 定位和修复结论来自可追溯程序证据，而不是只展示模型回答；
3. pytest/sandbox 与语义检查决定修复是否成立；
4. 120 次 live LLM/Hybrid 试验已完成，成功、失败、blocker、成本和延迟都能
   追溯到冻结协议与 RunRecord。

主演示不依赖网络和付费模型。公开 GitHub 运行和 live-model smoke 是可选附加项，
避免现场网络、限流或 Key 问题占用全部时间。

## 2. 演示前准备

### 2.1 干净环境

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

无法激活时，后续把 `python` 替换为 `.\.venv\Scripts\python.exe`。不要在
演示环境中自动运行未知仓库的 `setup.py` 或高风险 build hook。

### 2.2 三个快速自检

```powershell
python -m code_intelligence_agent agent --help

python -m pytest -q `
  tests/test_v3_release_evaluation.py `
  tests/test_v3_phase6_evaluation.py

python -m code_intelligence_agent.evaluation.release_hygiene_audit `
  outputs_demo/v3_release_hygiene `
  --root . `
  --require-pass `
  --format markdown
```

预期结果：CLI 能显示 Agent 参数；聚焦测试通过；release hygiene 为 5/5。

### 2.3 API Key 原则

主演示不需要 Key。可选 live smoke 只允许在当前 PowerShell 进程注入 fresh key：

```powershell
$env:CIA_LLM_API_KEY = "<fresh-key>"
```

禁止把 Key 写入 `.env`、README、脚本、截图、测试或 artifact。演示结束后清理：

```powershell
Remove-Item Env:CIA_LLM_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
```

## 3. 主演示命令

### 3.1 展示并重建 V3 最终发布报告

```powershell
Get-Content docs/v3/phase7_unified_evaluation.md

python -m code_intelligence_agent v3-release-eval `
  outputs_demo/v3_release `
  --root . `
  --live-evaluation outputs_v3/phase3_live_20260717_334eee/evaluation.json `
  --require-complete `
  --format markdown
```

第一条命令不需要网络，直接展示提交的脱敏最终证据。第二条命令只在本地保留
原始 live artifact 时重建报告；它读取 Phase 0-6 证据、live evaluation 和
RunRecord hash。当前正确输出是：

- `status=pass`；
- `offline_release_status=pass`；
- `complete_release_status=pass`、`claim_eligible=true`；
- `120/120` trial、`423/423` RunRecord audit pass；
- LLM pass@1/pass@3 为 `0.40/0.50`，Hybrid 为 `0.30/0.45`；
- pending requirements 为 `none`。

如果省略 `--live-evaluation`，新生成的报告会按设计回到 `partial`，因为发布器
不会从已提交摘要反推出原始 live trial。这不改变已提交 final artifact 的状态。

### 3.2 可选：真实公开仓库 Agent

网络稳定时可执行：

```powershell
python -m code_intelligence_agent agent `
  pypa/sampleproject `
  outputs_demo/sampleproject_agent `
  --execution-profile agent-auto `
  --preset smoke `
  --planner-mode hybrid `
  --auto-controller-max-actions 2 `
  --repository-test-timeout 30 `
  --agent-time-budget-seconds 300 `
  --user-goal "理解仓库、诊断测试环境，并只报告有证据支持的缺陷" `
  --format markdown
```

该命令会真实执行 GitHub discovery、源码筛选、程序分析和受控 controller
action。仓库没有 failing evidence 时，正确结果可能是 clean analysis 或 blocker，
而不是强行生成补丁。

网络失败时不要临时关闭安全策略。直接展示已提交的 Phase 2/4/6/7 artifact，
并将网络错误解释为 provider/environment blocker。

### 3.3 可选：终端多轮会话

完成 Agent 运行后，把生成的 session 文件传给聊天入口：

```powershell
python -m code_intelligence_agent chat-ui `
  --session outputs_demo/sampleproject_agent/agent_session.json `
  --format markdown
```

可演示的自然语言输入：

```text
解释 Top-1 函数为什么排在第一位
不要修改公共 API，也不要增加依赖
列出当前 blocker 和下一步可执行动作
:execute on
继续执行一个低风险动作
查看本轮使用了哪些记忆
exit
```

对话并不让模型获得任意执行权。命令意图仍要映射到 Action Registry，并经过
Schema、风险、状态和预算检查。

## 4. 10 分钟讲解脚本

### 0:00-0:45：一句话定位

```text
这是一个面向真实 Python GitHub 仓库的代码智能 Agent。它把程序分析、
真实失败测试、多证据函数定位、Rule/LLM/Hybrid 补丁、sandbox 验证和
失败反思放进 Observe -> Plan -> Act -> Verify -> Reflect -> Replan 闭环；
模型负责语义建议，控制器负责权限，pytest 和语义 oracle 负责成功判定。
```

### 0:45-2:00：展示 Agent 架构

打开
[`v3_architecture_and_agent_design_cn.md`](v3_architecture_and_agent_design_cn.md)
中的总体架构图和状态机，重点讲：

- Observation 是仓库、图、测试、预算和记忆的结构化状态；
- LLM Planner 输出 proposal，不直接执行；
- Action Registry 与 Safety Gate 授予执行权限；
- 工具结果改变下一轮 observation，因此不是固定脚本；
- 失败补丁 fingerprint 和新 traceback 进入 Reflection。

### 2:00-3:15：展示真实 benchmark

打开 [`phase1_verification.json`](phase1_verification.json)：

- 25 个候选，20 accepted、5 rejected；
- 6 个真实仓库，固定 bug/fix SHA；
- bug 目标测试必须失败，fix 目标测试和完整回归必须通过；
- gold patch 对模型不可见；
- rejected case 保留原因，不从失败分母中静默删除。

### 3:15-4:15：展示仓库环境鲁棒性

打开 [`phase2_verification.json`](phase2_verification.json)：

- 20/20 结构化报告和测试命令；
- 19/20 测试进程真实启动并终止；
- 剩余 1 个输出明确 environment blocker；
- V2 7/20 与 V3 19/20 协议不同，所以不做因果提升宣称。

### 4:15-6:00：讲 FinalScore 与消融

展示冻结 `simplex-021`：

```text
0.225 * SBFL
+ 0.250 * Semantic
+ 0.175 * TestFailure
+ 0.100 * StackTrace
+ 0.125 * Complexity
+ 0.125 * ChangeHistory
```

说明 141 个候选 profile 只在 validation split 搜索，test ground truth 没有传入。
冻结 test 的 Top-1/3/5 为 0.60/0.80/1.00。移除 Dynamic 后 Top-1 下降
0.40，移除 Semantic 后下降 0.20；Graph 和 Rule 在被选 profile 中权重为 0，
因此不能虚构它们在该 test split 上的收益。

### 6:00-7:15：讲补丁与成功权威

展示 Rule/LLM/Hybrid 三条候选来源和统一验证链：

```text
candidate -> AST -> scope/signature/safety -> targeted tests
          -> full regression -> semantic validation -> verified repair
```

当前 Rule 在 20 个真实案例上 pass@1=0，这是保留的正式结果。真实 LLM 的
pass@1/pass@3 为 0.40/0.50，Hybrid 为 0.30/0.45；Hybrid 的 22 个 verified
winning record 全部来自 LLM generator family，因此不声称 Hybrid 或 Rule
带来 uplift。LLM Judge 只能排序，不能把失败测试判成成功。

### 7:15-8:15：讲语义验证、记忆和安全

语义校准使用 2 个人工 fix，2/2 通过且 3/3 reverse mutation 被杀死；强调
这是 validator calibration，不是 Agent 修复。

记忆受控实验从 3/7 completion 到 7/7，过期、冲突和 advisory 越权均为 0。
安全套件 8/8 受控 fixture 被拒绝、隔离或准确报告；说明这仍不是容器级证明。

### 8:15-9:30：现场运行统一发布器

执行 3.1 的 `v3-release-eval` 命令，展示：

- 七个 offline phase gate 均为 pass；
- live 120/120、RunRecord 423/423 和 exact model audit 均为 pass；
- LLM/Hybrid pass@1/pass@3 的 Wilson 区间保留小样本不确定性；
- 119 个 trial 的伪完整 artifact 会被拒绝；
- model ID 或 Prompt hash 漂移也会被拒绝；
- 直接成功、Reflection 成功、失败和 provider timeout 都有脱敏实例。

### 9:30-10:00：用边界收尾

```text
项目已经完成真实 benchmark、19/20 环境启动、困难定位、Rule 基线、语义门、
记忆、安全和统一审计，并完成 60 LLM + 60 Hybrid live trial。LLM 在三次内
修复 10/20 个 case，Hybrid 修复 9/20；失败和 blocker 均保留。完整回归与
clean archive 结果以 Phase 7 final verification 为准。
```

## 5. 面试时打开的五个文件

| 顺序 | 文件 | 证明内容 |
| --- | --- | --- |
| 1 | `docs/v3/v3_architecture_and_agent_design_cn.md` | Agent 闭环与权限边界 |
| 2 | `docs/v3/phase1_verification.json` | 真实 bug benchmark 与防泄漏 |
| 3 | `docs/v3/phase4_localization_metrics.json` | FinalScore、冻结测试和消融 |
| 4 | `docs/v3/phase6_verification.json` | 记忆与安全结果 |
| 5 | `docs/v3/phase7_unified_evaluation.json` | 所有指标状态与发布门 |

## 6. 可选 live-model smoke

该步骤会产生实际费用，不属于 10 分钟主演示。确认 fresh key 和 provider 中的
精确模型可用后，先运行单案例、两种策略 smoke：

```powershell
python -m code_intelligence_agent v3-repair-eval `
  outputs_v3/phase3_live `
  --root . `
  --strategies llm,hybrid `
  --case-id bugsinpy-pysnooper-3 `
  --live-model `
  --max-workers 1 `
  --format markdown
```

单案例成功后再移除 `--case-id` 续跑全部案例。默认支持 resume；不要使用
`--no-resume` 重复已完成 trial。provider retry 只属于原 trial，不算新 trial。

完整试验结束后执行：

```powershell
python -m code_intelligence_agent v3-release-eval `
  outputs_v3/phase7_complete `
  --root . `
  --live-evaluation outputs_v3/phase3_live_20260717_334eee/evaluation.json `
  --require-complete `
  --format markdown
```

只有命令零退出且报告 `status=pass`，才能更新简历中的 live repair 数字。

## 7. 演示失败降级

| 外部问题 | 正确处理 |
| --- | --- |
| GitHub 网络失败 | 展示 committed benchmark/environment artifact，记录 network blocker |
| Key 缺失或认证失败 | 展示 provider failure 与 rule fallback，不算代码修复失败 |
| 模型不可用 | 保留 exact model blocker，不临时换模型后混入同一实验 |
| 依赖安装失败 | 输出 environment blocker，不执行未知高风险脚本 |
| 仓库没有 failing test | 输出 clean analysis，不伪造补丁 |
| live trial 不满 120 | 保持完整发布 `pending`，不计算缺失值 |

## 8. 演示前最终检查

- `git status --short` 为空；
- API Key 只在当前进程，截图中不可见；
- README、CLI help 与演示命令一致；
- 每个数字都能链接到 JSON artifact；
- Rule、LLM、Hybrid、human calibration 和 controlled fixture 明确区分；
- 网络和模型不可用时，仍能在 10 分钟内完成离线演示。
