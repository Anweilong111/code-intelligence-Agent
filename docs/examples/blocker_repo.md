# 示例 2：Blocker 仓库 `octocat/Hello-World`

## 输入命令

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence https://github.com/octocat/Hello-World --agent --format markdown
```

## Agent 做了什么

1. **Observe**：识别输入是 GitHub URL，ref 为 `master`，但源码导入阶段没有得到可分析 Python 文件。
2. **Plan**：当前阶段标记为 `source_import_blocked`，下一步目标是回到 `phase1_repo_understanding`。
3. **Act**：选择 `adjust_source_filters`，建议放宽 include / target-prefix / source filter 后重新发现源码。
4. **Verify**：确认没有可用 Python source、无静态信号、无动态测试证据。
5. **Reflect**：将问题归因为仓库类型或分析范围不匹配，而不是测试失败或补丁失败。
6. **Replan**：给出终止建议：改变分析范围，或提供外部动态证据。

## 关键输出

| 字段 | 结果 |
| --- | --- |
| Current Stage | `source_import_blocked` |
| Next Stage | `phase1_repo_understanding` |
| Primary Blocker | `source_import_or_parse_missing` |
| Selected Action | `adjust_source_filters` |
| Action Executable Now | `true` |
| Stop Recovery Policy | `change_analysis_scope_or_supply_external_evidence` |

Agent Controller 计划链路：

| Step | 含义 |
| ---: | --- |
| 1 | 读取结构、图、静态信号、动态证据、goal readiness |
| 2 | 根据 blocker 选择 `adjust_source_filters` |
| 3 | 重新运行源码发现或放宽过滤 |
| 4 | 检查是否产生 `repository_structure.json` |
| 5 | 如果仍失败，归类 blocker |
| 6 | 选择下一步恢复动作或终止诊断 |

## 为什么这个示例重要

真实 GitHub 仓库并不总是 Python 项目。这个示例证明系统不会把无 Python 源码仓库误判成“分析失败”，而是输出明确 blocker、恢复策略和可执行下一步。这是 Agent 与普通脚本的重要区别：它能识别当前状态不满足继续修复的前置条件，并给出可审计的决策链。

