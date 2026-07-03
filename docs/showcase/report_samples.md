# 报告样例：真实 GitHub 仓库输出摘录

本页不是完整原始输出，而是把真实 acceptance artifact 压缩成面试可读的报告样例。完整输出位于：

`outputs_smoke/repo_intelligence_agent_cli_default_output_acceptance_current/`

## 新增摘录：依赖与打包画像进入 Agent Observe

这个摘录用于说明 P2 鲁棒性增强后的报告字段。Agent 不会在 Observe 阶段强行安装复杂依赖，而是先把仓库配置转成可审计信号，供 Plan / Act / Verify / Reflect / Replan 使用。

输入仓库形态：

```text
pyproject.toml
uv.lock
tox.ini
noxfile.py
src/demo/__init__.py
src/demo/core.py
```

关键输出：

| 字段 | 示例值 | 说明 |
| --- | --- | --- |
| `project_config_files` | `pyproject.toml, uv.lock, tox.ini, noxfile.py` | Observe 到的项目配置 |
| `dependency_tool_signals` | `pyproject, tox, uv, nox` | 识别出的依赖/runner 工具 |
| `dependency_file_count` | `2` | 依赖配置证据数量 |
| `packaging_file_count` | `1` | 打包配置证据数量 |
| `test_runner_config_files` | `tox.ini, noxfile.py` | 测试 runner 配置证据 |

Agent 决策价值：

| Loop Step | 摘要 |
| --- | --- |
| Observe | 读取项目配置、依赖工具、测试 runner 和 src-layout |
| Plan | 判断优先使用 tox/nox/pytest，或进入环境 blocker 分支 |
| Act | 生成测试执行计划或依赖准备建议 |
| Verify | 根据 runner 是否存在、测试是否可执行判断是否推进 |
| Reflect | 如果 runner 缺失，归因为环境问题而不是代码修复失败 |
| Replan | 输出安装依赖、使用 fallback runner 或要求外部测试命令 |

## 样例 A：可测试仓库 `pytest-dev/pluggy`

输入：

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence https://github.com/pytest-dev/pluggy --agent --format markdown
```

Agent 观察到：

- repo input kind: GitHub URL
- test runner: pytest
- recommended command: `python -m pytest -q testing`
- dynamic evidence level: passing tests
- current blocker: `dynamic_evidence_not_usable:passing_tests`

测试结果：

```text
139 passed in 0.23s
```

Top-k suspicious functions：

| Rank | Function | File | FinalScore | Source Role |
| ---: | --- | --- | ---: | --- |
| 1 | `TagTracer.get` | `pluggy/_tracing.py` | 1.0 | application |

Agent 决策：

| Loop Step | 摘要 |
| --- | --- |
| Observe | 读取 repo、测试结果、静态信号、Top-k 定位 |
| Plan | passing tests 不能直接作为 bug 修复证据 |
| Act | 记录 regression guard |
| Verify | 确认测试通过且无 failing evidence |
| Reflect | 当前缺少 bug oracle |
| Replan | 要求提供 failing test、bug report 或 mutation ground truth |

结论：Agent 没有强行生成补丁，而是输出可审计 blocker。

## 样例 B：非 Python / 无源码 blocker `octocat/Hello-World`

输入：

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence https://github.com/octocat/Hello-World --agent --format markdown
```

关键结果：

| 字段 | 值 |
| --- | --- |
| Current Stage | `source_import_blocked` |
| Primary Blocker | `source_import_or_parse_missing` |
| Selected Action | `adjust_source_filters` |
| Recovery Policy | `change_analysis_scope_or_supply_external_evidence` |

Agent 决策：

| Loop Step | 摘要 |
| --- | --- |
| Observe | 没有发现可分析 Python source |
| Plan | 回到 phase1 repo understanding |
| Act | 尝试放宽 source filter 或 target-prefix |
| Verify | 未产生可分析源码则保持 blocker |
| Reflect | 问题属于输入仓库类型/分析范围，而不是补丁失败 |
| Replan | 要求改变分析范围或提供外部证据 |

结论：Agent 能识别不满足分析前置条件的仓库，并输出下一步。

## 样例 C：Patch Validation + Reflection `TheAlgorithms/Python`

输入：

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence https://github.com/TheAlgorithms/Python --agent --format markdown --include ciphers/gronsfeld_cipher.py --run-repository-test-command
```

Top-k suspicious functions：

| Rank | Function | File | Mode | FinalScore |
| ---: | --- | --- | --- | ---: |
| 1 | `gronsfeld` | `ciphers/gronsfeld_cipher.py` | dynamic | 0.2324 |

Patch validation：

| 字段 | 值 |
| --- | --- |
| Status | `pass` |
| Reason | `patch_validation_reflection_success` |
| Input Candidates | 2 |
| Executed Candidates | 4 |
| Successful Candidates | 1 |
| Reflection Generated | 2 |
| Reflection Successful | 1 |

成功补丁：

```diff
--- a/ciphers/gronsfeld_cipher.py
+++ b/ciphers/gronsfeld_cipher.py
@@ -21,6 +21,8 @@
     """
     ascii_len = len(ascii_uppercase)
     key_len = len(key)
+    if not key_len:
+        return 0
     encrypted_text = ""
     keys = [int(char) for char in key]
     upper_case_text = text.upper()
```

Agent 决策：

| Loop Step | 摘要 |
| --- | --- |
| Observe | 读取失败测试、动态证据和 gronsfeld 候选 |
| Plan | 进入 phase3 patch validation |
| Act | 生成 `missing_len_zero_guard` 候选补丁 |
| Verify | depth=0 补丁失败，记录 failure type |
| Reflect | 根据失败输出生成 depth=1 refined candidate |
| Replan | 选择通过目标 pytest 的 refined patch，并保留 regression caveat |

结论：这是项目最能体现 Agent 的样例：失败后不是停止，而是利用执行反馈进行 reflection。
## P3 9 仓库矩阵报告位置

完整 P3 展示索引见：

[p3_product_robustness_matrix.md](p3_product_robustness_matrix.md)

P3 product-robustness suite 会为每个仓库写出一个可审计报告目录：

`outputs_smoke/repo_intelligence_p3_product_robustness_current/`

重点报告样例：

| Repository | Report Path | What To Inspect |
| --- | --- | --- |
| `Textualize/rich` | `rich_p3_complex_pyproject_console/github_repo_intelligence.md` | complex pyproject, source-limited analysis, AgentController next action |
| `tiangolo/fastapi` | `fastapi_p3_complex_pyproject_applications/github_repo_intelligence.md` | complex pyproject, large repo slicing, no-static-candidate blocker |
| `karpathy/nanoGPT` | `nanogpt_p3_no_test_command_blocker/github_repo_intelligence.md` | no-test-command diagnosis and blocker reporting |
| `TheAlgorithms/Python` | `thealgorithms_p3_repair_reflection/github_repo_intelligence.md` | patch validation and reflection success |

Suite 级报告：

`outputs_smoke/repo_intelligence_p3_product_robustness_current/github_repo_intelligence_suite.md`

用 P3 索引展示公开仓库鲁棒性；当面试官想听一个具体 Agent 决策路径时，再使用上面的 pluggy / Hello-World / TheAlgorithms 三个压缩样例。
