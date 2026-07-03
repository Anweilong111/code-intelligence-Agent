# P6 Agent 优化进度审计

本文档记录 P6 目标的当前推进状态。P6 仍在进行中，不能宣称已经完成“任意 GitHub 仓库全自动修复”。当前已经完成 Phase 1，并开始推进 Phase 2 的真实仓库 onboarding matrix 基础设施。

## 当前完成项

### Phase 1：AgentController Action Registry 与 Policy Trace

已新增标准动作注册表：

- 代码：`code_intelligence_agent/agents/action_registry.py`
- Artifact：`agent_action_registry.json/md`
- 覆盖 P6 要求的 13 个核心 action

已覆盖 action：

- `clone_or_load_repository`
- `discover_repository_structure`
- `discover_tests`
- `diagnose_environment`
- `run_repository_tests`
- `localize_fault`
- `generate_llm_patch_candidates`
- `generate_hybrid_patch_candidates`
- `validate_patch_in_sandbox`
- `run_llm_patch_reflection_loop`
- `run_llm_patch_judge`
- `emit_blocker_report`
- `generate_final_agent_report`

每个 action 均包含：

- `action_id`
- `phase`
- `tool`
- `module`
- `input_requirements`
- `expected_artifact`
- `success_condition`
- `failure_condition`
- `blocker_type`
- `retry_policy`
- `next_possible_actions`
- `aliases`

已新增 Policy Trace：

- Artifact：`agent_policy_trace.json/md`
- 内容：把当前 selected action 映射到 canonical action，并展开 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` 六步证据。

AgentController 每次写出 controller artifact 时，会同时写出：

- `github_repo_agent_controller.json/md`
- `agent_action_registry.json/md`
- `agent_policy_trace.json/md`

### Phase 2：真实 GitHub 仓库 Onboarding Matrix 基础设施

本轮新增：

- 代码：`code_intelligence_agent/evaluation/github_onboarding_matrix.py`
- Artifact：`github_onboarding_matrix.json/md`
- 测试：`tests/test_github_onboarding_matrix.py`
- 示例：`outputs_smoke/p6_onboarding_matrix_synthetic/github_onboarding_matrix.json`
- 示例：`outputs_smoke/p6_onboarding_matrix_synthetic/github_onboarding_matrix.md`

矩阵会读取一个或多个 `github_repo_intelligence.json` / `github_repo_agent.json` / 输出目录，并汇总：

- 仓库输入类型：`owner/repo`、GitHub URL、本地路径。
- Python 源码数量与是否为无 Python 源码项目。
- src layout、package root、推荐 target prefix。
- `pyproject.toml`、`requirements*.txt`、`setup.py`、`setup.cfg`、`tox.ini`、`noxfile.py`、`pytest.ini`。
- tests 数量、测试目录、测试框架信号、候选测试命令、runner 分布。
- 测试环境状态、依赖缺失、推荐安装命令。
- 测试执行状态、timeout、failing test evidence。
- Agent policy trace 的 selected action / canonical action。
- 每个仓库是否具备 P6 要求的 onboarding artifact。

本轮也把测试发现信息独立成 core artifact：

- `repository_test_discovery.json`
- `repository_test_discovery.md`

该 artifact 从 `repository_structure.test_structure` 和 repository profile 中抽取测试发现结果；即使仓库没有 tests，也会输出 `oracle:no_tests` blocker，而不是让信息隐含在大 JSON 里。

当前 matrix 已支持检查 P6 Phase 2 要求的 10 类场景：

- 普通 pytest 项目
- src layout 项目
- pyproject 项目
- requirements 项目
- tox/nox 项目
- 无 Python 源码项目
- 无 tests 项目
- 依赖缺失项目
- 测试超时项目
- 可产生 failing test evidence 的项目

注意：当前完成的是 matrix 基础设施和本地合成测试。至少 10 个真实 GitHub 仓库的实跑矩阵还未完成。

## 当前示例 artifact

LLM controller selected action 示例：

- `outputs_smoke/llm_controller_action_examples/llm_controller_action_examples.json`
- `outputs_smoke/llm_controller_action_examples/llm_patch_selected/agent_policy_trace.json`
- `outputs_smoke/llm_controller_action_examples/llm_reflection_selected/agent_policy_trace.json`
- `outputs_smoke/llm_controller_action_examples/hybrid_missing_key_selected/agent_policy_trace.json`

现有 P5 DeepSeek smoke case 已刷新出 P6 artifact：

- `outputs_smoke/repo_intelligence_llm_repair_smoke_deepseek_direct_attempt/thealgorithms_gronsfeld_llm_repair/agent_action_registry.json`
- `outputs_smoke/repo_intelligence_llm_repair_smoke_deepseek_direct_attempt/thealgorithms_gronsfeld_llm_repair/agent_policy_trace.json`
- `outputs_smoke/repo_intelligence_llm_repair_smoke_deepseek_pass/thealgorithms_gronsfeld_llm_repair/agent_action_registry.json`
- `outputs_smoke/repo_intelligence_llm_repair_smoke_deepseek_pass/thealgorithms_gronsfeld_llm_repair/agent_policy_trace.json`
- `outputs_smoke/repo_intelligence_hybrid_no_key_showcase/thealgorithms_gronsfeld_hybrid_no_key/agent_action_registry.json`
- `outputs_smoke/repo_intelligence_hybrid_no_key_showcase/thealgorithms_gronsfeld_hybrid_no_key/agent_policy_trace.json`

## 已验证的 Policy 选择

| 场景 | Selected Action | Canonical Action | Status |
| --- | --- | --- | --- |
| LLM patch ready | `generate_llm_patch_candidates` | `generate_llm_patch_candidates` | `pass` |
| LLM reflection ready | `run_llm_patch_reflection_loop` | `run_llm_patch_reflection_loop` | `pass` |
| Hybrid LLM key missing | `generate_hybrid_patch_candidates` | `generate_hybrid_patch_candidates` | `pass` |
| Repair/report ready | `run_search_and_ablation_evaluation` | `generate_final_agent_report` | `pass` |

## 当前验证命令

已通过：

```bash
python -m pytest tests/test_agent_controller.py -q
python -m pytest tests/test_github_onboarding_matrix.py tests/test_github_repo_intelligence.py::test_artifact_inventory_flags_missing_current_stage_required_artifacts tests/test_github_repo_intelligence.py::test_artifact_inventory_requires_failure_overlay_artifacts_when_attempted -q
```

当前结果：

- `tests/test_agent_controller.py`：`32 passed`
- Phase 2 matrix / artifact inventory 定向测试：`4 passed`

## 后续未完成项

P6 仍未完成，后续应继续推进：

1. Phase 2：跑至少 10 个真实 GitHub 仓库，生成真实 `github_onboarding_matrix.json/md`。
2. Phase 2：确保每个真实仓库都输出 `repository_profile`、`repository_structure`、`repository_test_discovery`、`repository_test_environment`、`repository_test_execution_plan`、`agent_policy_trace`。
3. Phase 3：LLM patch multi-candidate，一次生成 3-5 个候选，并展示非第一个候选成功的 case。
4. Phase 4：Reflection strategy 深化，覆盖 test failure、safety blocked、reflection 后仍失败 blocker。
5. Phase 5：LLM judge 校准报告，指标化 judge-sandbox agreement。
6. Phase 6：扩展到 20 个 repair/evaluation case。
7. Phase 7：只有 P6 真实完成后，再更新最终 GitHub 展示、简历和面试材料，且不夸大能力。
