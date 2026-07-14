# Code Intelligence Agent V2 现场演示指南

本文用于在干净 Windows PowerShell 环境中完成 10 分钟演示。演示重点是 Agent 如何根据 evidence 选择动作、如何验证结论以及何时诚实停止，不以等待一次不确定的 live LLM 调用作为主要展示方式。

## 1. 演示前准备

### 1.1 干净环境安装

在仓库根目录执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

如果 PowerShell 阻止当前进程加载激活脚本，可只对当前进程设置策略：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

不激活环境也可以直接使用 `.\.venv\Scripts\python.exe` 代替后续命令中的 `python`。

### 1.2 快速自检

```powershell
python -m pytest -q `
  tests/test_intent_routing_evaluation.py `
  tests/test_planner_strategy_evaluation.py `
  tests/test_memory_ablation_evaluation.py `
  tests/test_patch_strategy_evaluation.py `
  tests/test_system_evaluation.py
```

然后检查 CLI：

```powershell
python -m code_intelligence_agent agent --help
python -m code_intelligence_agent chat-ui --help
```

### 1.3 API Key 配置

API Key 只放在当前 PowerShell 进程的环境变量中。不要写入 `.env`、README、命令脚本、测试或截图。

```powershell
$env:CIA_LLM_PROVIDER = "deepseek"
$env:CIA_LLM_MODEL = "<supported_model_name>"
$env:CIA_LLM_API_KEY = "<your_key>"

$env:CIA_LLM_REPLAN_ENABLED = "1"
$env:CIA_REPLAN_LLM_PROVIDER = $env:CIA_LLM_PROVIDER
$env:CIA_REPLAN_LLM_MODEL = $env:CIA_LLM_MODEL
$env:CIA_REPLAN_LLM_API_KEY = $env:CIA_LLM_API_KEY

$env:CIA_INTENT_LLM_ENABLED = "1"
$env:CIA_INTENT_LLM_PROVIDER = $env:CIA_LLM_PROVIDER
$env:CIA_INTENT_LLM_MODEL = $env:CIA_LLM_MODEL
$env:CIA_INTENT_LLM_API_KEY = $env:CIA_LLM_API_KEY
```

可选的 Judge 使用独立变量；它只参与候选排序与风险审计：

```powershell
$env:CIA_JUDGE_PROVIDER = $env:CIA_LLM_PROVIDER
$env:CIA_JUDGE_MODEL = $env:CIA_LLM_MODEL
$env:CIA_JUDGE_API_KEY = $env:CIA_LLM_API_KEY
```

演示结束后清理当前会话中的敏感变量：

```powershell
Remove-Item Env:CIA_LLM_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:CIA_REPLAN_LLM_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:CIA_INTENT_LLM_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:CIA_JUDGE_API_KEY -ErrorAction SilentlyContinue
```

## 2. 一键运行命令

### 2.1 无 Key 的稳定演示

该命令仍运行完整受控 Agent；LLM 不可用时 Planner 与意图路由记录规则 fallback。

```powershell
python -m code_intelligence_agent agent `
  pytest-dev/iniconfig `
  outputs_demo/iniconfig `
  --execution-profile agent-auto `
  --preset mining `
  --planner-mode hybrid `
  --auto-controller-max-actions 2 `
  --repository-test-timeout 30 `
  --format markdown
```

### 2.2 接入 LLM 的完整 Agent

先完成环境变量配置，再执行：

```powershell
python -m code_intelligence_agent agent `
  pypa/sampleproject `
  outputs_demo/sampleproject_llm `
  --execution-profile agent-auto `
  --preset mining `
  --planner-mode hybrid `
  --repository-patch-generation-mode hybrid `
  --repository-test-reflection-mode llm `
  --repository-test-reflection-rounds 1 `
  --patch-judge-mode llm `
  --auto-controller-max-actions 3 `
  --agent-time-budget-seconds 600 `
  --agent-llm-cost-budget-usd 0.50 `
  --user-goal "分析失败测试，定位根因，并只提交经过 sandbox 验证的最小补丁" `
  --format markdown
```

重要边界：仓库本身没有 failing evidence 时，正确结果可能是 clean-repo 报告或 blocker，而不是补丁。`--patch-judge-mode llm` 不改变 pytest/sandbox 的最终裁决权。

### 2.3 终端多轮自然语言对话

Agent 运行后，从输出中的 `agent_session.json` 读取 `session_id`，也可以直接把 session 文件路径传给聊天入口：

```powershell
python -m code_intelligence_agent chat-ui `
  --session outputs_demo/sampleproject_llm/agent_session.json `
  --format markdown
```

对话示例：

```text
解释为什么 Top-1 函数排在第一位
不要修改公共 API，也不要增加新依赖
比较当前三个补丁候选
继续修复，但最多再运行一轮反思
:execute on
重新运行目标测试
查看本轮使用了哪些记忆
exit
```

默认聊天只准备下一步动作；输入 `:execute on` 后，命令型意图才允许执行。自然语言先经过 LLM Function Calling 或规则 fallback，随后仍必须映射到 Action Registry。

## 3. 10 分钟现场演示脚本

### 0:00-1:00：一句话定位

说明这是“程序分析 + 受控 LLM 规划 + sandbox 验证”的代码智能 Agent。目标不是声称修复所有仓库，而是让成功、失败和 blocker 都可验证、可审计。

### 1:00-2:30：展示架构与闭环

打开 [`architecture_and_design.md`](architecture_and_design.md)，重点讲：

- Observe 读取 repository evidence、memory 与预算。
- LLM Planner 只产生结构化 proposal。
- Safety Gate 决定动作是否合法。
- pytest/sandbox 决定补丁是否成功。
- 失败后将新约束与 fingerprint 写入 memory，再 Replan。

### 2:30-4:30：执行公开仓库 Agent

运行无 Key 的 `pytest-dev/iniconfig` 命令。运行过程中不要只等待终端；同时说明系统将产生哪些 artifact。若网络或依赖较慢，立即切换到已提交的公开仓库案例，不让演示被外部服务绑定。

### 4:30-6:00：阅读三个关键 artifact

按以下顺序打开：

1. `github_repo_intelligence.md`：最终仓库分析结论。
2. `agent_decision_report.md`：为什么选择当前 action。
3. `agent_execution_trace.md`：动作是否真实执行、返回码与证据路径。

如果 clean repo 测试通过，强调“不生成补丁”是正确决策。

### 6:00-7:30：讲 FinalScore

展示 Coverage-aware 与 Static-only 两套权重，说明动态证据缺失时对应信号为零，而不是估算一个值。用 contribution 分解解释 Top-k，而不是只读总分。

### 7:30-8:30：讲修复与 Reflection

打开 [`phase8_case_studies.md`](phase8_case_studies.md) 中的 `normalize` 和 `parse_port`：一个初始候选直接成功，一个初始候选失败后通过受限 reflection 恢复。强调两者都是离线受控 fixture，成功权威仍是 sandbox tests。

### 8:30-9:30：讲安全与 blocker

展示 `delete_repository` 被 Action Registry 拒绝，以及 ItsDangerous 缺少 `freezegun` 时停止自动修复。说明 Agent 能行动，也必须知道何时不能行动。

### 9:30-10:00：用真实指标收尾

只引用可追溯数字：20/20 陌生仓库输出结构化报告；7/20 真正启动并终止测试进程；Phase 7 完成八组对照；受控 Planner 最终已注册动作率 1.0；完整回归记录为 1226 passed。随后主动说明 graph/dynamic 消融未产生增益和 live-model benchmark 仍是后续工作。

## 4. Artifact 检查清单

一次完整 Agent 运行后，至少检查：

| 类别 | Artifact | 面试中回答的问题 |
| --- | --- | --- |
| 总结 | `github_repo_intelligence.md` | 仓库最终发生了什么 |
| 状态 | `repository_profile.md` | layout、依赖、runner 是什么 |
| 结构 | `repo_graph.md` | 调用和依赖结构如何建模 |
| 定位 | `fault_localization.md` | Top-k 为什么这样排序 |
| 测试 | `repository_test_execution_result.md` | 测试是否真正运行 |
| 规划 | `agent_decision_report.md` | 为什么选择该动作 |
| 执行 | `agent_execution_trace.md` | 该动作是否真的执行 |
| 安全 | `repository_test_patch_validation.md` | 候选为何通过或拒绝 |
| 反思 | `reflection_trace.md` | 失败如何改变下一轮 |
| 记忆 | `agent_memory_report.md` | 哪些历史证据影响了决策 |
| 终止 | blocker/final report | 为什么成功、停止或等待外部输入 |

## 5. 外部失败时的演示降级

### GitHub 网络失败

不要临时修改系统来绕过安全策略。说明网络属于外部 blocker，然后使用已提交案例 [`top_level_agent_live_smoke.md`](../examples/top_level_agent_live_smoke.md) 和 Phase 6 固定 SHA 结果继续讲解。

### LLM provider 超时或 Key 无效

展示 planner trace 中的 provider failure 与 `fallback_to_rule_planner`。这恰好证明降级路径真实存在。不要把 provider 错误描述为 Agent 算法失败。

### 依赖安装失败

展示 environment blocker，不在现场执行高风险安装脚本。系统应保留静态分析结果并给出下一步命令建议。

### 仓库没有失败测试

展示 clean-repo 结果；不要人为篡改公开仓库。需要演示修复时，切换到已提交的受控 patch fixture 报告。

## 6. 演示前最终检查

```powershell
git status --short
python -m code_intelligence_agent.evaluation.release_hygiene_audit `
  outputs_demo/release_hygiene `
  --root . `
  --format markdown `
  --require-pass
```

确认：

- 没有 API Key、`.env`、本地 outputs 或二进制文档进入 Git candidate set。
- README 中的命令与当前 CLI `--help` 一致。
- 所引用数字能打开对应 JSON/Markdown artifact。
- 受控 fixture、公开仓库运行和 live provider 结果被清楚区分。
- 现场演示即使没有网络和模型，也能用已提交证据完整讲完。
