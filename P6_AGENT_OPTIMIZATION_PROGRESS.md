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

本轮继续新增旧报告回填能力：

- CLI 参数：`--backfill-derived-artifacts`
- Dry-run 参数：`--dry-run-backfill`
- 能力：从已有 `github_repo_intelligence.json` / `github_repo_agent.json` 中推导并补齐缺失的 `repository_profile`、`repository_structure`、`repository_test_discovery`、`repository_test_environment`、`repository_test_execution_plan` 和 `agent_policy_trace` artifact。
- 路径兼容：当旧报告中记录的相对 `output_dir` 已失效时，matrix 会回退到 report 所在目录查找 artifact，避免把历史输出误判为缺失。

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

注意：当前完成的是 matrix 基础设施、旧报告回填能力、本地合成测试，以及对既有 10 个真实仓库报告的本地回填验证。该回填验证使用 ignored 的 `outputs_smoke/` 历史输出，不会作为仓库交付物提交；新的 10 仓库可复现实跑矩阵仍是后续工作。

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

### Phase 3：LLM Patch 多候选与 Prompt Audit

本轮新增：

- `LLMPatchGenerator.generate()` 支持一次 LLM 响应返回多个 `fixed_sources`。
- `build_patch_prompt()` 增加 `candidate_count`、`top_k_suspicious_functions`、`failing_test_nodeids`、`failure_evidence`、`public_api_evidence`、`call_graph_context` 和 `previous_failed_patch_fingerprints` 字段。
- 每个 LLM patch candidate metadata 记录：
  - `candidate_id`
  - `llm_candidate_index`
  - `llm_candidate_count_requested`
  - `prompt_context_audit`
  - `response_parse`
  - `validation`
  - `safety_gate`
- `repository_test_patch_candidates.json/md` 增加 `llm_generation_audit`，记录每次 LLM 调用的 requested / parsed / accepted / rejected candidate 数量和 prompt context 缺口。

注意：当前补强的是多候选生成、prompt 审计和 artifact 记录能力；后续仍需要用真实 DeepSeek/兼容 LLM 在多 case 中跑出“非第一个 LLM candidate 成功”的 sandbox 证据。

### Phase 4：Reflection Strategy 与 Safety Evidence Audit

本轮新增：

- `RepairLoop` 与 `BeamPatchSearch` 的 reflection child candidate 会基于自身 `old_source/new_source` 重新执行 AST/scope/signature safety gate，不再复用 parent candidate 的旧 `safety_gate` metadata。
- `mutable_default_arg` 等允许签名变更的规则在 reflection safety gate 中继续沿用 `allow_signature_change_for_rules`，避免把合法的规则修复误判为 unsafe。
- 如果 refined child patch 被 safety gate 阻断，系统返回 `safety_gate_blocked`，命令记录为 `["safety_gate"]`，不会进入真实 pytest，也不会伪装成 sandbox 成功。
- `reflection_trace.json/md` 现在记录 parent patch fingerprint、parent failure type、parent sandbox result、基于 parent failure type 选择的 reflection strategy、refined child patch fingerprint、safety gate result、sandbox result、`reflection_evidence_complete` 和 `reflection_evidence_missing`。
- `LLMPatchGenerator.refine_many()` 现在为 LLM reflection prompt 显式加入 `parent_candidate`、`previous_patch`、`target_function_source`、`reflection_strategy`、`failure_evidence`、`related_caller_callee_context` 和可选 `judge_feedback`，并在每个 refined candidate metadata 中记录 `reflection_prompt_context_audit`、`response_parse`、`reflection_strategy` 与 `last_reflection_audit`。
- `repository_test_patch_validation.json/md` 与 `reflection_trace.json/md` 会提升 `llm_reflection_audit` / `llm_reflection_attempt_count`，用于审计 LLM reflection 的 prompt 字段缺口、JSON/schema 解析结果、accepted/rejected candidate 数量和 rejection taxonomy。

注意：当前完成的是 reflection 执行、安全门和 LLM prompt/parse 审计链路增强；P6 Phase 4 仍需要继续补真实 DeepSeek/兼容 LLM reflection case，尤其是 test failure -> reflection success、safety blocked -> reflection/blocker、reflection 后仍失败 -> blocker 这三类真实 case。

### Phase 5：LLM Patch Judge 与 Sandbox 校准证据

本轮补齐候选级 patch judge 的 outcome audit：

- `repository_test_patch_validation.json/md` 现在输出 `patch_judge_outcome_counts`，把每个带 `patch_judgment` 的候选按 judge verdict 与 sandbox success/failure 对齐统计。
- 单仓库 `github_repo_intelligence.json/md`、suite summary 和 LLM repair matrix 会透传 `repository_test_patch_judge_outcome_counts`、`accept_success`、`reject_failure`、`accept_failure`、`reject_success` 等字段。
- `llm_repair_metrics_report.json/md` 新增 P6 target：`llm_patch_judge_ready`、`llm_patch_judge_accept_success`、`llm_patch_judge_reject_failure`。只有出现 LLM judge ready case、judge 接受 sandbox 成功候选、judge 拒绝 sandbox 失败候选时，Phase 5 的矩阵目标才算通过。
- `accept_failure` 和 `reject_success` 会作为 judge/sandbox outcome mismatch 暴露出来，用于审计 LLM judge 是否过度乐观或过度保守；最终成功仍由 `sandbox_pytest_decides_success` 决定。

### Phase 6：LLM Repair Evaluation Matrix 基础设施

本轮新增 P6 命名评估 artifact：

- `llm_repair_evaluation_matrix.json`
- `llm_repair_evaluation_matrix.md`
- `llm_repair_metrics_report.json`
- `llm_repair_metrics_report.md`

这些 artifact 由 `code_intelligence_agent/evaluation/llm_repair_showcase_matrix.py` 写出，并与既有 `llm_repair_showcase_matrix.json/md` 兼容。新增 metrics report 会集中汇总：

- LLM Direct Success Rate
- Reflection Success Rate
- Patch Success@1 / @3 / @5
- Safety Gate Block Rate
- Sandbox Pass Rate
- Judge-Sandbox Agreement
- Patch Judge Outcome Counts
- Average Runtime
- LLM token / estimated cost 统计
- Blocker Type Distribution
- Blocker Category Distribution：`llm_failed_blocker`、`environment_blocker`、`no_test_oracle_blocker`、`safety_gate_blocker`
- 每个 case 的 `Observe -> Plan -> Act -> Verify -> Reflect -> Replan` trace 完整性
- 每个 LLM direct success、LLM reflection success 和 blocker case 的 evidence completeness：provider/model、key 审计、候选数量、sandbox authority、patch validation artifact、LLM reflection audit、blocker next action 等字段缺失时会标记为 `evidence_status=review`

注意：当前完成的是 Phase 6 评估 artifact、指标基础设施、证据完整性门禁和 blocker taxonomy 门禁，不代表已经满足 P6 要求的 20 个 repair/evaluation case、5 个 LLM direct success、3 个 LLM reflection success 和 5 个 blocker case。

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
python -m pytest tests/test_llm_patch_generator.py tests/test_repository_test_patch_candidates.py -q
python -m pytest tests/test_llm_repair_showcase_matrix.py tests/test_github_repo_intelligence_suite.py::test_intelligence_suite_llm_preflight_blocks_missing_keys_before_runner tests/test_github_repo_intelligence_suite.py::test_intelligence_suite_llm_showcase_thresholds_recompute_report_passed -q
python -m pytest tests/test_repository_test_patch_validation.py tests/test_beam_patch_search.py tests/test_agent_controller.py -q
python -m pytest tests/test_llm_patch_generator.py tests/test_repository_test_patch_validation.py -q
python -m pytest tests/test_agent_controller.py tests/test_repository_test_patch_candidates.py -q
python -m pytest tests/test_github_repo_intelligence_suite.py tests/test_llm_repair_showcase_matrix.py -q
python -m pytest tests/test_phase3_patch_and_sandbox.py tests/test_phase4_search_and_evaluation.py -q
python -m pytest tests -q
python -m code_intelligence_agent.evaluation.github_onboarding_matrix --backfill-derived-artifacts --output-dir outputs_smoke/p6_onboarding_matrix_real_existing_backfilled <10 existing report paths>
```

当前结果：

- `tests/test_agent_controller.py`：`32 passed`
- Phase 2 matrix / artifact inventory 定向测试：`5 passed`
- Phase 3 LLM patch generator / candidate artifact 定向测试：`19 passed`
- Phase 6 LLM repair matrix 定向测试：`7 passed`
- GitHub repo intelligence suite 回归测试：`57 passed`
- Phase 4 reflection safety / Beam / AgentController 回归测试：`55 passed`
- Phase 4 LLM reflection prompt / patch validation 定向测试：`26 passed`
- AgentController / repository patch candidates 回归测试：`40 passed`
- GitHub repo intelligence suite / LLM repair matrix 回归测试：`62 passed`
- Phase 3 / Phase 4 搜索与沙箱回归测试：`66 passed`
- 完整测试：`1023 passed`
- 旧报告回填验证：`backfill_status=pass`、`matrix_status=pass`、`case_count=10`

## 后续未完成项

P6 仍未完成，后续应继续推进：

1. Phase 2：重新跑至少 10 个真实 GitHub 仓库，生成可复现的真实 `github_onboarding_matrix.json/md`，而不是只依赖历史输出回填。
2. Phase 2：确保每个新跑真实仓库都输出 `repository_profile`、`repository_structure`、`repository_test_discovery`、`repository_test_environment`、`repository_test_execution_plan`、`agent_policy_trace`。
3. Phase 3：用真实 DeepSeek/兼容 LLM 跑出 3-5 个候选的多 case 证据，并展示非第一个候选成功的 sandbox case。
4. Phase 4：reflection safety gate、trace evidence audit 与 LLM reflection prompt/parse audit 已增强；后续仍需补真实 DeepSeek/兼容 LLM reflection case，覆盖 test failure、safety blocked、reflection 后仍失败 blocker。
5. Phase 5：LLM judge 校准报告，指标化 judge-sandbox agreement。
6. Phase 6：扩展到 20 个 repair/evaluation case，并用 `llm_repair_evaluation_matrix.json/md` 与 `llm_repair_metrics_report.json/md` 验证 5 个 direct success、3 个 reflection success、5 个 blocker case。
7. Phase 7：只有 P6 真实完成后，再更新最终 GitHub 展示、简历和面试材料，且不夸大能力。
