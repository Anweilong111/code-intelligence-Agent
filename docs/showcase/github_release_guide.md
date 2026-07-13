# GitHub 仓库展示指南

本指南用于把项目整理成可以上传 GitHub、用于简历和面试展示的版本。原则是：只写已经验证的能力，明确自动修复边界，不暴露任何 API key，不提交本地运行缓存。

## 项目定位

> 面向公开 Python GitHub 仓库的代码智能 Agent。用户输入 `owner/repo`、GitHub URL 或本地仓库路径后，系统自动完成仓库发现、源码筛选、结构建模、静态缺陷信号挖掘、函数级 Top-k 缺陷定位、测试环境诊断、可选测试执行、补丁候选生成、sandbox 验证、LLM reflection 和 blocker 报告，并通过 `AgentController` 输出 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 的可审计闭环。

不要写：

- 任意语言仓库都支持。
- 任意 GitHub 仓库都能 100% 自动修复真实 bug。
- 不要把 LLM judge 写成最终成功标准，或写成 pytest sandbox 替代品。
- 缺少 failing test 或 test oracle 时仍能可靠修复。

## 推荐阅读顺序

1. `README.MD`：项目首页、架构、Agent 闭环、V1 指标和运行方式。
2. `docs/examples/v1_evaluation_summary.md`：当前 V1 评估结果，9/9 required metrics measured。
3. `docs/examples/v1_sample_reports.md`：可放到 GitHub 的真实仓库样例报告。
4. `RESUME_AGENT_PROJECT.md`：简历 bullet 和中英文写法。
5. `INTERVIEW_QA_AGENT_PROJECT.md`：面试讲法和常见追问。

## V1 验证摘要

当前 V1 evidence snapshot：

| 指标 | 结果 |
| --- | ---: |
| GitHub onboarding repositories | 30/30 |
| Repair/evaluation cases | 50 |
| Required metric contracts | 9/9 |
| Directly measured metrics | 9/9 |
| Proxy metrics | 0 |
| Missing evidence metrics | 0 |
| Agent loop | `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` |

核心指标包括：`onboarding_success_rate`、`topk_localization_accuracy`、`pass_at_1`、`pass_at_k`、`reflection_uplift`、`blocker_accuracy`、`sandbox_success_rate`、`average_runtime_ms` 和 `llm_cost_usd`。

V1 metric summary 可通过以下命令生成：

```bash
python -m code_intelligence_agent.evaluation.v1_evaluation_summary ^
  outputs/v1_evaluation_summary ^
  --readiness-audit outputs/v1_readiness_dataset_audit/v1_readiness_dataset_audit.json ^
  --onboarding-suite outputs/v1_onboarding_aggregate/v1_onboarding_slice_aggregate.json ^
  --repair-metrics outputs/v1_repair/llm_repair_metrics_report.json ^
  --repair-catalog-audit outputs/v1_repair/llm_repair_case_catalog_audit.json ^
  --localization-report outputs/v1_repair/phase4_search_evaluation.json ^
  --llm-cost-report outputs/v1_cost/llm_cost_evidence.json ^
  --require-pass
```

## 推荐简介版本

> 代码智能 Agent：基于 AST / Call Graph / Program Graph / SBFL-style scoring 的 GitHub Python 仓库缺陷定位与受控自动修复系统，支持 AgentController 闭环规划、LLM patch/reflection、pytest sandbox 验证与 blocker 审计；V1 评估覆盖 30 个真实 GitHub onboarding case、50 个 repair/evaluation case，并完成 9/9 required metrics measured。

推荐 bullet：

- 设计并实现面向公开 Python GitHub 仓库的代码智能 Agent，支持仓库发现、源码筛选、结构建模、测试诊断、函数级 Top-k 缺陷定位、补丁候选生成、sandbox 验证和可审计报告输出。
- 构建基于 AST / Call Graph / Program Graph 的多信号定位框架，融合静态规则、图传播、动态测试证据和 SBFL-style score 得到 `FinalScore`。
- 实现 `AgentController` 决策闭环，基于 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 自动选择测试执行、环境诊断、LLM patch、reflection、sandbox validation 或 blocker 输出。
- 构建 V1 evaluation summary，覆盖 onboarding success、Top-k localization、Pass@1、Pass@k、reflection uplift、blocker accuracy、sandbox success、runtime 和 LLM cost。

## 面试 1 分钟讲法

我做的是一个面向公开 Python GitHub 仓库的代码智能 Agent，不是简单调用大模型改代码。系统先解析仓库，构建 AST、Call Graph 和 Program Graph，再融合静态规则、图传播、动态测试证据和 SBFL-style score 得到函数级 Top-k 定位。AgentController 根据当前 artifacts 和 blockers 执行 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`，决定运行测试、诊断环境、生成补丁、验证补丁、进入 reflection 或输出 blocker。所有补丁必须经过 JSON/AST/scope/safety gate 和 pytest sandbox，LLM judge 只参与排序，不替代执行验证。

## GitHub 首页检查清单

- README 说明项目定位、架构图、AgentController loop、V1 指标、运行命令和安全边界。
- 示例材料包含 2-3 个真实仓库分析报告。
- 简历材料包含中文 bullet、英文 bullet、1 分钟讲法和 3 分钟讲法。
- 面试材料覆盖 Agent vs workflow、程序图、FinalScore、sandbox、reflection、LLM judge 和边界。
- 不提交任何原始 API key。
- 不出现“100% 自动修复任意仓库”的夸大表述。
- 不把 LLM judge 写成最终成功标准。
- 不提交 `outputs_smoke`、`outputs_live`、`htmlcov` 等本地运行产物。

## Release Hygiene Gate

发布前运行：

```bash
python -m code_intelligence_agent.evaluation.release_hygiene_audit ^
  outputs_smoke/release_hygiene_audit_current ^
  --format json ^
  --require-pass
```

最终 V1 completion audit：

```bash
python -m code_intelligence_agent.evaluation.v1_goal_completion_audit ^
  outputs_smoke/v1_goal_completion_audit_current ^
  --format json ^
  --require-pass
```

该检查会扫描 Git candidate set，验证没有原始 API key、被跟踪的本地输出、
Word 二进制产物、错误的 LLM judge 权限表述，以及公开文档中的明显工具署名。

## 边界说明

这个项目适合写成“面向公开 Python GitHub 仓库的智能分析与受控修复 Agent”。当仓库缺少 Python 源码、测试命令、可复现环境、failing test、test oracle 或安全修复条件时，Agent 会输出 blocker 和下一步建议，而不是声称自动修复成功。
