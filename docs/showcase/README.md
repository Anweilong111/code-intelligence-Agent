# GitHub Showcase Pack

这个目录用于增强 GitHub 展示观感：面试官可以先看这里，再决定是否深入阅读完整 README、项目报告或源码。

## 快速入口

| 材料 | 用途 |
| --- | --- |
| [github_release_guide.md](github_release_guide.md) | GitHub 发布、简历投递和面试展示前的最终阅读入口与验收清单 |
| [p3_product_robustness_matrix.md](p3_product_robustness_matrix.md) | P3 9 仓库真实验证矩阵，包含每个仓库的 stage、blocker、selected action、next action 和 artifact 路径 |
| [report_samples.md](report_samples.md) | 展示真实运行报告长什么样，包含可测试、blocker、patch reflection 三类样例 |
| [robustness_matrix.md](robustness_matrix.md) | 展示当前真实仓库覆盖能力、blocker 类型和后续有限鲁棒性优化路线 |
| [../examples/README.md](../examples/README.md) | 三个真实 GitHub 仓库的详细示例 |
| [../../RESUME_AGENT_PROJECT.md](../../RESUME_AGENT_PROJECT.md) | 简历 bullet 和 1/3/5 分钟讲法 |
| [../../INTERVIEW_QA_AGENT_PROJECT.md](../../INTERVIEW_QA_AGENT_PROJECT.md) | 面试问答 |
| [../../PROJECT_REPORT_BEGINNER_GUIDE.md](../../PROJECT_REPORT_BEGINNER_GUIDE.md) | 初学者友好的完整项目报告 |

## 架构图

![System Architecture](../../project_report_diagrams/architecture.png)

## 缺陷定位分数融合

![Score Fusion](../../project_report_diagrams/score_fusion.png)

## Program Graph 建模

![Program Graph](../../project_report_diagrams/program_graph.png)

## Patch Validation + Reflection

![Repair Loop](../../project_report_diagrams/repair_loop.png)

## Benchmark / Evaluation Loop

![Benchmark Loop](../../project_report_diagrams/benchmark_loop.png)

## 当前验收快照

最近一次 acceptance suite：

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite datasets/github_cases/repo_intelligence_agent_cli_default_output_acceptance.example.json outputs_smoke/repo_intelligence_agent_cli_default_output_acceptance_current --require-success
```

关键结果：

| 指标 | 结果 |
| --- | ---: |
| Runs | 5 |
| Agent Passed Runs | 5/5 |
| Objective Compliance Pass Runs | 5/5 |
| Agent Controller Loop Complete Runs | 5/5 |
| Patch Validation Successes | 1 |
| Reflection Successes | 1 |
| Targeted tests | `57 passed` |

## 展示结论

当前项目已经达到“强算法向 Agent 简历项目”的展示标准：它有真实 GitHub 仓库样例、有 AgentController 决策链、有程序图和多信号定位算法、有 sandbox patch validation 与 reflection 证据，也明确保留了无测试、无 Python 源码、依赖缺失等真实仓库 blocker。

## P3 产品化鲁棒性快照

更强的公开仓库矩阵定义在：

`datasets/github_cases/repo_intelligence_p3_product_robustness.example.json`

详细展示索引：

[p3_product_robustness_matrix.md](p3_product_robustness_matrix.md)

已验证报告目录：

`outputs_smoke/repo_intelligence_p3_product_robustness_current/`

它覆盖 9 个仓库，包含 owner/repo 输入、GitHub URL 输入、pinned refs、默认分支发现、source cache 复用、src-layout、复杂 pyproject、tox/nox 信号、无 Python blocker、无测试命令 blocker，以及 1 条 patch/reflection 修复链路。
