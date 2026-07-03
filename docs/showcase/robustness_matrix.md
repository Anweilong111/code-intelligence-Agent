# 有限鲁棒性矩阵

本页用于说明当前项目在真实 Python GitHub 仓库上的覆盖范围，以及后续 3-5 天应该优先增强哪些鲁棒性点。

## 当前覆盖的真实仓库场景

| 场景 | 示例仓库 | 当前能力 | Agent 输出 |
| --- | --- | --- | --- |
| 可测试 Python 仓库 | `pytest-dev/pluggy` | 识别 pytest，执行窄范围测试，记录 regression guard | passing tests blocker + next action |
| Python 示例项目 | `pypa/sampleproject` | 识别测试命令、环境 blocker 和静态定位结果 | environment / dynamic evidence blocker |
| 非 Python 或无源码仓库 | `octocat/Hello-World` | 识别 source import blocker | `source_import_or_parse_missing` |
| 无测试命令仓库 | `karpathy/nanoGPT` | 输出 no-test-command / static fallback 类 blocker | `expand_static_candidate_search` |
| patch/reflection 修复 | `TheAlgorithms/Python` | Top-k 定位、候选补丁、sandbox validation、reflection 成功 | patch validation pass |

## 当前已经覆盖的 blocker 类型

| Blocker | 含义 | 当前处理方式 |
| --- | --- | --- |
| `source_import_or_parse_missing` | 没有可分析 Python source 或解析失败 | 调整 include/exclude/target-prefix，或要求改变分析范围 |
| `dynamic_evidence_not_usable:passing_tests` | 测试全通过，没有 failing evidence | 记录 regression guard，要求 failing test / bug report / ground truth |
| `no_static_candidates` | 静态候选不足 | 扩展静态候选搜索或提供外部证据 |
| `environment:test_tool_missing` | 测试 runner 或依赖不可用 | 输出测试环境诊断和安装建议 |
| `patch_candidates_not_ready` | 缺少可验证补丁上下文 | 要求更多动态证据或 overlay rule |

## 后续 3-5 天优先增强项

这些增强项直接服务于“真实仓库分析更稳定”，不改变项目主线：

1. **tox / nox 增强**  
   增强 `tox.ini`、`noxfile.py` 的 runner 识别、fallback 和 blocker 文案。

2. **poetry / uv / pyproject 增强**  
   识别 `pyproject.toml` 中的 build backend、dependency groups 和 test extras，并给出更准确的安装建议。

3. **多包项目和 src-layout 增强**  
   强化 `src/<package>`、多 package root、namespace package 的 source filtering 和 test command PYTHONPATH 处理。

4. **超时和复杂测试增强**  
   对长时间 pytest 增加 timeout narrowing，优先选择低风险 narrow tests，再输出可审计 timeout blocker。

5. **更多真实仓库 smoke case**  
   增加 3-5 个代表性仓库，覆盖 requests/click/rich/fastapi 类项目，但每个样例只纳入对展示有帮助的稳定路径。

## 不作为当前阶段目标

- 不追求任意语言支持。
- 不承诺任意 GitHub 仓库都能自动修复真实 bug。
- 不把无 failing test 的仓库包装成“已修复”。
- 不为了展示效果绕过 sandbox validation。

## 验收建议

每次增强后至少运行：

```bash
python -m pytest tests/test_readme_showcase_consistency.py tests/test_github_repo_intelligence_suite.py -q
```

## P2 新增依赖与打包画像证据

当前鲁棒性增强已经把依赖管理、打包配置和测试 runner 配置统一纳入 `repository_profile`、`github_repo_intelligence` 和 `AgentController` 的 Observe 阶段。这样做的目的不是强行安装复杂依赖，而是让 Agent 在真实 GitHub 仓库中先判断“仓库看起来应该如何安装、如何测试、哪里可能阻塞”。

| 能力点 | 覆盖信号 | 输出位置 | Agent 价值 |
| --- | --- | --- | --- |
| 现代依赖管理 | `uv.lock`, `poetry.lock`, `pdm.toml`, `hatch.toml`, `Pipfile` | `dependency_manager_profile.tool_signals` | Observe 阶段识别依赖工具，Plan 阶段选择安装建议或 blocker |
| Python 打包配置 | `pyproject.toml`, `setup.cfg`, `setup.py` | `packaging_file_count`, `packaging_files` | 区分 src-layout、editable install 和 legacy packaging |
| 测试 runner 配置 | `tox.ini`, `noxfile.py`, `pytest.ini` | `test_runner_config_files`, `test_command_candidates` | runner 缺失时输出 `environment:test_tool_missing`，并给出 fallback |
| 仓库结构布局 | `src/<package>`, root package, 多 package root | `package_structure`, `layout_hints` | 减少 Top-k 定位落到测试脚本或自动化脚本上的概率 |

对应测试覆盖：

```bash
python -m pytest tests/test_github_repository_profile.py tests/test_github_repo_intelligence.py::test_github_repo_intelligence_defaults_to_static_analysis_summary tests/test_agent_controller.py::test_agent_controller_observes_dependency_and_packaging_profile -q
```

如果新增真实仓库 acceptance case，再运行对应 manifest 的 `github_repo_intelligence_suite --require-success`。
## P3 9 仓库产品化鲁棒性矩阵

详细索引见：

[p3_product_robustness_matrix.md](p3_product_robustness_matrix.md)

复现命令：

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence_suite datasets/github_cases/repo_intelligence_p3_product_robustness.example.json outputs_smoke/repo_intelligence_p3_product_robustness_current --reuse-existing-reports --require-success
```

P3 矩阵把公开 GitHub 仓库覆盖扩展到 9 个：
`pypa/sampleproject`, `pytest-dev/pluggy`, `psf/requests`, `pallets/click`,
`Textualize/rich`, `tiangolo/fastapi`, `TheAlgorithms/Python`,
`octocat/Hello-World`, and `karpathy/nanoGPT`.

关键验证结果：

| Metric | Result |
| --- | ---: |
| Runs | 9 |
| Agent passed runs | 9/9 |
| Objective compliance pass runs | 9/9 |
| AgentController loop complete runs | 9/9 |
| Repository structure modeled runs | 8 |
| Repo graph ready runs | 8 |
| Source import blocker runs | 1 |
| No-test-command blocker runs | 1 |
| Patch/reflection repair-ready runs | 1 |

这个矩阵刻意分成两层：大多数仓库走轻量 AgentController planning，保证广泛公开仓库覆盖可以快速复现；代表性仓库继续覆盖环境诊断与 repair/reflection 深链路。这样既保持 Agent 方向，也不会夸大为每个仓库都能自动修复。
