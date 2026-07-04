# GitHub 发布与展示指南

本指南用于把项目整理成 GitHub、简历和面试可展示的版本。核心原则是：只写已经验证的能力，不夸大自动修复范围，不暴露任何 API key。

## 项目定位

> 面向公开 Python GitHub 仓库的代码智能分析与受控修复 Agent。用户输入 `owner/repo`、GitHub URL 或本地仓库路径后，系统自动完成仓库发现、源码筛选、结构建模、静态缺陷信号挖掘、函数级 Top-k 缺陷定位、测试环境诊断、可选测试执行、补丁候选生成、sandbox 验证、LLM reflection 和 blocker 报告，并通过 `AgentController` 输出 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 的可审计闭环。

不要写：

- 任意语言仓库都支持。
- 任意 GitHub 仓库都能 100% 自动修复真实 bug。
- LLM judge 可以替代 pytest sandbox。
- 缺少 failing test 或 test oracle 时仍能可靠修复。

## 推荐阅读顺序

1. `README.MD`：项目首页、架构、P6 结果和运行方式。
2. `RESUME_AGENT_PROJECT.md`：简历 bullet 和中英文写法。
3. `INTERVIEW_QA_AGENT_PROJECT.md`：面试讲法和常见追问。
4. `docs/examples/README.md`：真实仓库样例说明。
5. `docs/showcase/p3_product_robustness_matrix.md`：早期 9 仓库鲁棒性矩阵。

## P6 验证摘要

最新 P6 readiness 聚合结果：

| 指标 | 结果 |
| --- | ---: |
| P6 readiness checks | 24/24 pass |
| Onboarding matrix cases | 10 |
| Onboarding matrix checks | 12/12 pass |
| Repair/evaluation cases | 30 |
| LLM direct success cases | 5 |
| LLM reflection success cases | 4 |
| LLM blocker cases | 21 |
| Reflection evidence complete | 3 |
| Declared catalog cases matched | 20/20 |
| Sandbox authority | `sandbox_pytest_decides_success` |

可复现入口：

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite ^
  datasets/github_cases/repo_intelligence_p6_onboarding_readiness.example.json ^
  outputs_smoke/repo_intelligence_p6_onboarding_readiness_current ^
  --format json --require-success
```

如果本地已有 reports：

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite ^
  datasets/github_cases/repo_intelligence_p6_onboarding_readiness.example.json ^
  outputs_smoke/repo_intelligence_p6_onboarding_readiness_cached ^
  --format json --require-success --reuse-existing-reports
```

## 简历推荐版本

> 代码智能 Agent：基于 AST / Call Graph / Program Graph / SBFL-style scoring 的 GitHub Python 仓库缺陷定位与自动修复系统，支持 AgentController 闭环规划、LLM patch/reflection、pytest sandbox 验证与 blocker 审计；P6 验证覆盖 10 个真实仓库 onboarding case、30 个 repair/evaluation case、5 个 LLM direct success、4 个 LLM reflection success 和 21 个 blocker case。

推荐 bullet：

- 设计并实现面向公开 Python GitHub 仓库的代码智能 Agent，支持仓库发现、源码筛选、结构建模、测试诊断、函数级 Top-k 缺陷定位、补丁候选生成、sandbox 验证和可审计报告输出。
- 构建基于 AST / Call Graph / Program Graph 的多信号定位框架，融合静态规则、图传播、动态测试证据和 SBFL-style score 得到 `FinalScore`。
- 实现 `AgentController` 决策闭环，基于 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 自动选择测试执行、环境诊断、LLM patch、reflection、sandbox validation 或 blocker 输出。
- 构建 P6 evaluation matrix 与 readiness audit，覆盖 direct success、reflection success、LLM failed blocker、environment blocker、no-test-oracle blocker 和 safety-gate blocker。

## 面试 1 分钟讲法

我做的是一个面向公开 Python GitHub 仓库的代码智能 Agent，不是简单调用大模型改代码。系统先解析仓库，构建 AST、Call Graph 和 Program Graph，再融合静态规则、图传播、动态测试证据和 SBFL-style score 得到函数级 Top-k 定位。AgentController 根据当前 artifacts 和 blockers 执行 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan`，决定运行测试、诊断环境、生成补丁、验证补丁、进入 reflection 或输出 blocker。所有补丁必须经过 JSON/AST/scope/safety gate 和 pytest sandbox，LLM judge 只参与排序，不替代执行验证。

## GitHub 首页检查清单

- README 说明项目定位、架构图、AgentController loop、P6 结果、运行命令和安全边界。
- 简历材料包含中文 bullet、英文 bullet、1 分钟讲法和 3 分钟讲法。
- 面试材料覆盖 Agent vs workflow、程序图、FinalScore、sandbox、reflection、LLM judge 和边界。
- 不出现原始 API key。
- 不出现“100% 自动修复任意仓库”的夸大描述。
- 不把 LLM judge 写成最终成功标准。
- 不提交 `outputs_smoke` 这类本地运行产物。

## 边界说明

这个项目可以写作“面向公开 Python GitHub 仓库的智能分析与受控修复 Agent”。当仓库缺少 Python 源码、测试命令、可复现依赖、failing test、test oracle 或安全修复条件时，Agent 会输出 blocker 和下一步建议，而不是声明修复成功。
