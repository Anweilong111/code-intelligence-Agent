# 示例 1：可测试 Python 仓库 `pytest-dev/pluggy`

## 输入命令

```bash
python -m code_intelligence_agent.evaluation.github_repo_intelligence https://github.com/pytest-dev/pluggy --agent --format markdown
```

验收样例中使用了 pinned ref、`agent-auto` profile，并限制源码入口到：

- `src/pluggy/__init__.py`
- `src/pluggy/_tracing.py`

## Agent 做了什么

1. **Observe**：读取 GitHub repo、ref、源码候选、项目配置、测试命令候选、静态信号、动态测试结果。
2. **Plan**：发现 repo 是可测试 Python 项目，优先执行测试发现和测试运行。
3. **Act**：执行窄范围 pytest 命令：`python -m pytest -q testing`。
4. **Verify**：测试通过，`139 passed in 0.23s`。
5. **Reflect**：自然测试全部通过，动态失败证据不可用于定位真实缺陷，因此不能继续声称要自动修复。
6. **Replan**：将通过的测试记录为 regression guard，并输出下一步：提供 failing test、bug report、mutation ground truth，或扩展 controlled failure-overlay 规则。

## 关键输出

测试执行结果：

| 字段 | 结果 |
| --- | --- |
| Status | `pass` |
| Command | `python -m pytest -q testing` |
| Runner | `pytest` |
| Test Count | `139` |
| Passed | `139` |
| Failed / Errors | `0 / 0` |

函数级 Top-k 缺陷定位：

| Rank | Function | File | FinalScore | Source Role |
| ---: | --- | --- | ---: | --- |
| 1 | `TagTracer.get` | `pluggy/_tracing.py` | `1.0` | `application` |

Agent Controller 最终状态：

| 字段 | 结果 |
| --- | --- |
| Current Stage | `phase2_static_graph_fault_localization` |
| Primary Blocker | `dynamic_evidence_not_usable:passing_tests` |
| Selected Action | `extend_failure_overlay_or_provide_bug_report` |
| Recommended Next Action | 提供 failing test、bug report、mutation ground truth，或增加 overlay rule |

## 为什么这个示例重要

这个示例说明 Agent 不是“只要仓库能跑测试就强行生成补丁”。当自然测试全部通过时，系统会保守地把测试结果作为回归保护，而不是虚构 bug。这样可以体现 Agent 的可审计决策能力：有证据才进入修复，没有失败证据就输出 blocker 和下一步。

