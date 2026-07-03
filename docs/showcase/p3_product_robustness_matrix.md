# P3 9 仓库产品化鲁棒性展示索引

本页是 P4 发布包装阶段的核心证据索引，用来说明当前项目已经不是只跑少量玩具样例，而是能对多种公开 Python GitHub 仓库完成智能分析、blocker 诊断、AgentController 决策记录和报告输出。

它不是“任意仓库 100% 自动修复 bug”的宣传页。P3 矩阵的准确结论是：系统可以面向公开 Python GitHub 仓库做仓库理解、结构建模、静态缺陷信号挖掘、函数级 Top-k 定位、测试环境诊断、可选测试执行、补丁验证和 blocker 报告；当缺少 failing test、测试 oracle、可复现依赖或 Python 源码时，Agent 会输出下一步动作，而不是虚构修复结果。

## 复现命令

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite datasets/github_cases/repo_intelligence_p3_product_robustness.example.json outputs_smoke/repo_intelligence_p3_product_robustness_current --reuse-existing-reports --require-success
```

Suite 级报告：

`outputs_smoke/repo_intelligence_p3_product_robustness_current/github_repo_intelligence_suite.md`

## 验证结果

| 指标 | 结果 |
| --- | ---: |
| Runs | 9 |
| Agent passed runs | 9/9 |
| Objective compliance pass runs | 9/9 |
| AgentController loop complete runs | 9/9 |
| Agent decision timeline complete runs | 9/9 |
| Repository structure modeled runs | 8 |
| Repo graph ready runs | 8 |
| Program graph available runs | 8 |
| Source import blocker runs | 1 |
| No-test-command blocker runs | 1 |
| Repository test environment diagnosed runs | 3 |
| Patch validation success runs | 1 |
| Reflection success runs | 1 |

## 9 个仓库分别展示什么

| 仓库 | 展示重点 | Current Stage | Blocker | Selected Action | Next Action | Artifact |
| --- | --- | --- | --- | --- | --- | --- |
| `pypa/sampleproject` | `nox` 项目、`unittest` fallback、环境诊断、passing tests 不能直接用于修复 | `phase2_static_graph_fault_localization` | `dynamic_evidence_not_usable:passing_tests` | `extend_failure_overlay_or_provide_bug_report` | overlay 未产生 usable failing evidence；需要提供 failing test、bug report，或按诊断准备 `nox` 环境 | `outputs_smoke/repo_intelligence_p3_product_robustness_current/pypa_sampleproject_p3_environment_blocker/github_repo_intelligence.md` |
| `pytest-dev/pluggy` | GitHub URL 输入、pinned ref、`src` layout、pytest 项目、静态 fallback | `phase2_static_graph_fault_localization` | `dynamic_evidence_not_usable` | `discover_repository_tests` | 用完整 checkout 和更宽的 source/test discovery 收集动态证据，再决定是否进入 synthetic overlay 或外部 bug 输入 | `outputs_smoke/repo_intelligence_p3_product_robustness_current/pluggy_p3_src_layout_pinned/github_repo_intelligence.md` |
| `psf/requests` | tox 项目、依赖敏感仓库、source limit、静态分析 fallback | `phase1_repo_understanding` | `no_static_candidates` | `run_repository_tests_with_checkout` | 收集 repository-test evidence，用动态证据驱动后续定位 | `outputs_smoke/repo_intelligence_p3_product_robustness_current/requests_p3_tox_dependency_slice/github_repo_intelligence.md` |
| `pallets/click` | `src` layout、include filter、pinned ref、pytest/tox runner signals | `phase1_repo_understanding` | `no_static_candidates` | `run_repository_tests_with_checkout` | 在 checkout 环境中收集测试证据，补足静态候选不足的问题 | `outputs_smoke/repo_intelligence_p3_product_robustness_current/click_p3_src_layout_pinned/github_repo_intelligence.md` |
| `Textualize/rich` | 复杂 `pyproject`、大仓库 source slicing、pytest 项目、依赖敏感场景 | `phase1_repo_understanding` | `no_static_candidates` | `run_repository_tests_with_checkout` | 用测试证据或更明确的 target prefix 缩小定位范围 | `outputs_smoke/repo_intelligence_p3_product_robustness_current/rich_p3_complex_pyproject_console/github_repo_intelligence.md` |
| `tiangolo/fastapi` | 复杂 `pyproject`、大型应用仓库、source limit、静态 fallback | `phase1_repo_understanding` | `no_static_candidates` | `run_repository_tests_with_checkout` | 继续收集 repository-test evidence 或通过 include/target-prefix 指定子模块 | `outputs_smoke/repo_intelligence_p3_product_robustness_current/fastapi_p3_complex_pyproject_applications/github_repo_intelligence.md` |
| `TheAlgorithms/Python` | patch validation、controlled failure overlay、reflection 修复成功 | `phase3_patch_validation` | `none` | `run_search_and_ablation_evaluation` | patch validation 已 repair-ready；可进入 search strategy 和 ablation 评估 | `outputs_smoke/repo_intelligence_p3_product_robustness_current/thealgorithms_p3_repair_reflection/github_repo_intelligence.md` |
| `octocat/Hello-World` | 无 Python 源码 blocker、source import blocked、分析范围诊断 | `source_import_blocked` | `source_import_or_parse_missing` | `adjust_source_filters` | 放宽 include/target-prefix 或更换分析范围；不能把非 Python 仓库伪装成已分析成功 | `outputs_smoke/repo_intelligence_p3_product_robustness_current/octocat_p3_no_python_blocker/github_repo_intelligence.md` |
| `karpathy/nanoGPT` | 默认分支发现、无测试命令 blocker、静态候选不足时的保守报告 | `phase1_repo_understanding` | `no_static_candidates` | `expand_static_candidate_search` | 扩大 source scope 和 candidate mining，或提供外部 failing evidence | `outputs_smoke/repo_intelligence_p3_product_robustness_current/nanogpt_p3_no_test_command_blocker/github_repo_intelligence.md` |

## 轻量规划与深链路验证的区别

P3 矩阵刻意分成两类运行：

- **轻量 AgentController planning**：大多数仓库使用 `agent-auto` 配置并限制自动动作预算，重点验证输入归一化、源码筛选、结构建模、blocker 判断、selected action 和报告输出。这类运行证明 Agent 能判断“下一步应该做什么”，不强行宣称已修复。
- **深链路 repair/reflection**：`TheAlgorithms/Python` gronsfeld case 走到 patch validation 和 reflection，证明补丁候选经过 sandbox 验证，失败补丁可以基于执行反馈生成 refined candidate。

这两类运行共同体现 Agent 特征：系统不是固定 workflow，而是根据当前证据和 blocker 决定继续测试、扩展源码、调整分析范围、进入补丁验证、停止并要求外部输入，或进入后续评估。

## 面试讲法

可以这样概括：

> P3 矩阵覆盖 9 个公开 GitHub 仓库，验证了 Agent 对 owner/repo、GitHub URL、pinned ref、source cache、src-layout、tox/nox、复杂 pyproject、大仓库 source limit、无 Python 源码、无测试命令和 patch/reflection 修复的处理能力。结果是 9/9 agent passed、9/9 objective compliance、9/9 AgentController loop complete、8/9 repo graph ready，并有 1 个 patch/reflection 成功样例。这个矩阵证明的是“广泛分析与 blocker 报告能力”，不是夸大任意仓库都能自动修复真实 bug。

