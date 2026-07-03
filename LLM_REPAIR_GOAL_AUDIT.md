# P5 DeepSeek/LLM 自动修复 Agent 验收审计

本文档用于对照 P5 goal，证明当前项目已经从规则型分析流程升级为真实 DeepSeek/LLM 参与的大模型自动修复 Agent。所有 API key 均只通过环境变量注入；代码、README、测试和 smoke artifact 中不保存原始 key。

## 总体结论

- Goal 状态：已满足 P5 核心闭环要求。
- 主链路：`AgentController -> LLM patch generation -> JSON/AST/scope/safety gate -> sandbox pytest -> LLM reflection -> LLM judge -> audit report`。
- 三类 showcase matrix：`llm_direct_success=1`、`llm_reflection_success=1`、`llm_blocker=1`，`status=pass`。
- 最终矩阵：`outputs_smoke/llm_repair_showcase_matrix_complete/llm_repair_showcase_matrix.json`

## 核心 artifact

| 类型 | Artifact |
| --- | --- |
| LLM 一次修复成功 | `outputs_smoke/repo_intelligence_llm_repair_smoke_deepseek_direct_attempt/github_repo_intelligence_suite.json` |
| LLM 首次失败后 reflection 成功 | `outputs_smoke/repo_intelligence_llm_repair_smoke_deepseek_pass/github_repo_intelligence_suite.json` |
| LLM key 缺失 blocker | `outputs_smoke/repo_intelligence_hybrid_no_key_showcase/github_repo_intelligence_suite.json` |
| 三类合并矩阵 | `outputs_smoke/llm_repair_showcase_matrix_complete/llm_repair_showcase_matrix.json` |
| 三类合并矩阵 Markdown | `outputs_smoke/llm_repair_showcase_matrix_complete/llm_repair_showcase_matrix.md` |
| AgentController selected action 示例 | `outputs_smoke/llm_controller_action_examples/llm_controller_action_examples.json` |

## Goal 要求对照

| 要求 | 当前证据 |
| --- | --- |
| DeepSeek/LLM 在主修复链路中真实调用 | direct/reflection suite 中 `repository_llm_patch_provider=deepseek`、`repository_llm_patch_model=deepseek-v4-pro`、`repository_llm_patch_generation_status=pass` |
| API key 只通过环境变量配置 | report 仅记录 key presence/source/fingerprint；密钥扫描无 `sk-...` 命中 |
| 缺少 key 时 graceful blocker | blocker case 中 `repository_llm_patch_generation_status=blocked`、`reason=missing_llm_api_key` |
| AgentController 明确接入 LLM 修复动作 | 每个 case 的 `github_repo_agent_controller.json/md` 包含 `llm_repair_action_audit`；`llm_controller_action_examples.json` 证明 selected action 会落到 LLM patch、LLM reflection 和 hybrid blocker 三类动作 |
| LLM patch 限制在定位结果附近并经过安全门 | `repository_test_patch_candidates.json` 记录 LLM candidate、repair context、AST/scope/safety gate；`RepairLoop` 会在 refined candidate 进入 sandbox 前补齐 safety gate |
| LLM patch 进入 sandbox pytest | `repository_test_patch_validation.json` 记录 command、return code、stdout/stderr preview、pass/fail |
| 至少一个 LLM patch 直接成功 | direct case：`repair_action_id=generate_llm_patch_candidates`、`success_count=1` |
| 至少一个 LLM reflection 成功 | reflection case：`reflection_action_id=run_llm_patch_reflection_loop`、`successful_reflection_count=2` |
| 至少一个 LLM blocker 案例 | blocker case：`repair_action_id=generate_hybrid_patch_candidates`、`blocker=missing_llm_api_key` |
| LLM judge 参与候选排序但不替代 sandbox | direct/reflection validation 中 `patch_judge_mode=llm`、`patch_judge_authority=sandbox_pytest_decides_success` |
| README / 简历 / 面试材料不夸大 | `README.MD` 和 `RESUME_AGENT_PROJECT.md` 已改为“大模型自动修复 Agent”口径，并保留边界说明 |

## 三类 showcase 的 Agent loop 证据

| Class | Repair Action | Reflection Action | Provider / Model | Judge | Blocker |
| --- | --- | --- | --- | --- | --- |
| `llm_direct_success` | `generate_llm_patch_candidates` | none | `deepseek / deepseek-v4-pro` | `llm / ready` | none |
| `llm_reflection_success` | `generate_llm_patch_candidates` | `run_llm_patch_reflection_loop` | `deepseek / deepseek-v4-pro` | `llm / ready` | none |
| `llm_blocker` | `generate_hybrid_patch_candidates` | none | `deepseek / deepseek-v4-pro` | `none / disabled` | `missing_llm_api_key` |

补充说明：早期 reflection smoke artifact 生成时，refined child 的 `safety_gate` 字段没有完整展开。当前代码已在 `RepairLoop` 中强制为 refined candidate 补齐 safety gate；同时已对 reflection 成功案例的最佳 refined patch 追加 `posthoc_artifact_safety_audit`，结果为 `status=pass`、`ast_valid=true`、`scope_limited=true`、`changed_lines=2`。这不是重新声称旧运行在执行前已有该字段，而是对保存下来的最佳 patch 做可审计 AST/scope 校验。

## 验证命令

```bash
python -m pytest tests/test_agent_controller.py tests/test_llm_patch_generator.py tests/test_repository_test_patch_candidates.py tests/test_llm_judge.py::test_deepseek_clients_accept_role_specific_timeout_env tests/test_llm_repair_showcase_matrix.py tests/test_github_repo_intelligence_suite.py::test_intelligence_suite_llm_preflight_blocks_missing_keys_before_runner tests/test_github_repo_intelligence_suite.py::test_intelligence_suite_llm_showcase_thresholds_recompute_report_passed tests/test_github_repo_intelligence_suite.py::test_intelligence_suite_llm_preflight_blocks_placeholder_key_before_runner tests/test_github_repo_intelligence_suite.py::test_intelligence_suite_llm_repair_smoke_manifest_requires_real_llm -q
```

当前结果：`55 passed`。

```bash
python -m py_compile code_intelligence_agent/agents/controller.py code_intelligence_agent/agents/llm_client.py code_intelligence_agent/agents/llm_patch_generator.py code_intelligence_agent/evaluation/repository_test_patch_candidates.py code_intelligence_agent/evaluation/github_repo_intelligence_suite.py code_intelligence_agent/evaluation/llm_repair_showcase_matrix.py
```

当前结果：通过。

```bash
rg -n "sk-[A-Za-z0-9][A-Za-z0-9._-]{20,}" code_intelligence_agent datasets docs tests README.MD PROJECT_REPORT_BEGINNER_GUIDE.md RESUME_AGENT_PROJECT.md INTERVIEW_QA_AGENT_PROJECT.md outputs_smoke/repo_intelligence_llm_repair_smoke_deepseek_direct_attempt outputs_smoke/repo_intelligence_llm_repair_smoke_deepseek_pass outputs_smoke/repo_intelligence_hybrid_no_key_showcase outputs_smoke/llm_repair_showcase_matrix_complete
```

当前结果：无命中。

## 边界

当前项目可以表述为“面向公开 Python GitHub 仓库的大模型自动修复 Agent”。不能表述为“任意 GitHub 仓库 100% 自动修复真实 bug”。当缺少 failing test、controlled failure overlay、可验证 oracle、依赖环境或 LLM key 时，Agent 必须输出 blocker 和下一步建议。
