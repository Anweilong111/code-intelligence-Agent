from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
import uuid

from code_intelligence_agent.agents.llm_client import (
    LLMRequestError,
    StaticLLMClient,
)
from code_intelligence_agent.core.repo_parser import RepoParser
from code_intelligence_agent.evaluation.v3_experiment_protocol import (
    load_experiment_protocol,
    validate_run_record,
    validate_run_records,
)
from code_intelligence_agent.evaluation.v3_repair_orchestrator import (
    PreparedV3RepairCase,
    build_v3_rule_candidates,
    prepare_v3_repair_case,
    run_v3_repair_trial,
)
from code_intelligence_agent.evaluation.v3_repair_execution import (
    build_v3_run_record,
    copy_v3_trial_workspace,
    create_v3_repair_client,
    execute_v3_patch_candidate,
    provider_blocker_execution,
)
from code_intelligence_agent.evaluation.v3_repair_evaluation import (
    _load_resumable_trial,
    audit_v3_evaluation_completeness,
    audit_v3_reproduction_seed,
    build_arg_parser,
    build_v3_trial_input_fingerprint,
    build_v3_repair_metrics,
    run_v3_repair_evaluation,
    summarize_v3_model_metadata,
)
from code_intelligence_agent.evaluation import v3_repair_evaluation as repair_eval
from code_intelligence_agent.evaluation import v3_repair_execution as repair_execution
from code_intelligence_agent.evaluation.v3_repair_scope import (
    select_v3_analysis_scope,
)
from code_intelligence_agent.evaluation.v3_repair_trial import (
    audit_v3_model_context,
    apply_v3_patch_candidate,
    build_v3_editable_regions,
    build_v3_model_context,
    parse_v3_patch_response,
    render_v3_model_prompt,
    sanitize_v3_untrusted_text,
    validate_v3_patch_candidate,
)


def test_model_context_excludes_gold_data_secrets_and_absolute_workspace_paths(
    tmp_path,
):
    root = tmp_path / "repository"
    root.mkdir()
    source = (
        "def suspicious(value):\n"
        "    def nested(item):\n"
        "        return item + 1\n"
        "    return nested(value)\n\n"
        "def helper(value):\n"
        "    return value\n"
    )
    (root / "app.py").write_text(source, encoding="utf-8")
    (root / "test_app.py").write_text(
        "def test_suspicious():\n    assert False\n",
        encoding="utf-8",
    )
    parsed = RepoParser().parse(root.resolve())
    functions = {function.name: function for function in parsed.functions}
    localization = {
        "scoring_profile": "evidence_v2",
        "score_formula": "weighted evidence fusion",
        "score_weights": {"graph": 0.25, "static": 0.45},
        "rankings": [
            _ranking(functions["test_suspicious"], 1, 0.9),
            _ranking(functions["suspicious"], 2, 0.8),
            _ranking(functions["nested"], 3, 0.7),
            _ranking(functions["helper"], 4, 0.6),
        ],
    }
    regions, skipped = build_v3_editable_regions(root, localization)
    secret = "s" + "k-" + "abcdefghijklmnop1234"
    external_path = r"C:\Users\interview-user\AppData\Local\Temp\failure.log"
    dynamic_evidence = {
        "failure_category": "test_assertion_failure",
        "failure_signal": "FAIL: test_suspicious",
        "diagnostic_summary": "one assertion failed",
        "selected_execution": {
            "failure_context": (
                f"{root}\\test_app.py failed; token={secret}; "
                f"log={external_path}"
            )
        },
        "failing_tests": [
            {
                "nodeid": "test_app.py::test_suspicious",
                "path": str(root / "test_app.py"),
                "test_name": "test_suspicious",
            }
        ],
        "traceback_frames": [
            {
                "path": "../../runtime/lib/traceback.py",
                "line": 10,
                "function_name": "format_exception",
            }
        ],
    }
    case = _case()

    context = build_v3_model_context(
        case,
        repository_root=root,
        dynamic_evidence=dynamic_evidence,
        localization=localization,
        editable_regions=regions,
        skipped_regions=skipped,
    )
    audit = audit_v3_model_context(context, case=case, repository_root=root)
    serialized = json.dumps(context)

    assert audit["status"] == "pass", audit["errors"]
    assert [region.function_name for region in regions] == ["suspicious", "helper"]
    assert {row["reason"] for row in skipped} == {
        "test_region_not_editable",
        "overlapping_region_already_selected",
    }
    assert str(root) not in serialized
    assert secret not in serialized
    assert external_path not in serialized
    assert "<redacted-secret>" in serialized
    assert "<external-path>" in serialized
    assert context["failure_evidence"]["traceback_frames"][0]["path"] == (
        "<external-path:traceback.py>"
    )
    assert audit["contains_absolute_local_path"] is False
    assert case["fix_commit_sha"] not in serialized
    assert case["ground_truth"]["patch_sha256"] not in serialized
    assert context["editable_regions"][0]["function_id"].startswith("app.py::")
    assert {
        row["function_name"] for row in context["localization"]["top_k"]
    } == {"suspicious", "helper"}
    assert render_v3_model_prompt(context).startswith("Return JSON only.")


def test_v3_rule_candidate_limit_is_not_consumed_by_test_functions(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "test_app.py").write_text(
        "def test_bad(items=[]):\n    return items\n",
        encoding="utf-8",
    )
    (root / "app.py").write_text(
        "def bad(items=[]):\n    return items\n",
        encoding="utf-8",
    )
    parsed = RepoParser().parse(root.resolve())
    functions = {function.name: function for function in parsed.functions}
    localization = {
        "status": "pass",
        "rankings": [
            _ranking(functions["test_bad"], 1, 0.9),
            _ranking(functions["bad"], 2, 0.8),
        ],
    }
    prepared = PreparedV3RepairCase(
        case=_case(),
        seed_repository=root.resolve(),
        dynamic_evidence={},
        analysis_scope={"analysis_paths": None},
        analysis_scope_ground_truth_audit={},
        localization=localization,
        editable_regions=[],
        model_context={},
        model_context_audit={},
        model_context_artifact="",
        preparation_artifacts={},
    )

    candidates = build_v3_rule_candidates(prepared, limit=1)

    assert len(candidates) == 1
    assert candidates[0]["editable_region"].path == "app.py"


def test_patch_response_is_scope_checked_safety_gated_and_applied(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "maths.py").write_text(
        "def ratio(total, count):\n    return total / count\n",
        encoding="utf-8",
    )
    function = RepoParser().parse(root.resolve()).functions[0]
    localization = {"rankings": [_ranking(function, 1, 0.95)]}
    regions, skipped = build_v3_editable_regions(root, localization)
    assert skipped == []
    region = regions[0]
    replacement = (
        "def ratio(total, count):\n"
        "    if count == 0:\n"
        "        raise ValueError('count must be non-zero')\n"
        "    return total / count"
    )
    response = json.dumps(
        {
            "analysis": "Guard the observed zero denominator.",
            "files": [
                {
                    "path": region.path,
                    "original_sha256": region.original_sha256,
                    "replacement": replacement,
                }
            ],
            "targeted_tests": ["tests/test_maths.py::test_zero"],
            "risk": "low",
            "assumptions": [],
        }
    )

    parsed = parse_v3_patch_response(response, editable_regions=regions)
    validation = validate_v3_patch_candidate(
        parsed["candidate"],
        editable_regions=regions,
        repository_root=root,
    )
    application = apply_v3_patch_candidate(
        parsed["candidate"],
        editable_regions=regions,
        repository_root=root,
    )

    assert parsed["status"] == "pass", parsed
    assert validation["status"] == "pass", validation
    assert validation["ast_valid"] is True
    assert validation["safety_gate"] == "pass"
    assert validation["total_changed_lines"] > 0
    assert application["status"] == "pass", application
    assert (root / "maths.py").read_text(encoding="utf-8") == replacement + "\n"


def test_small_module_region_is_safety_gated_and_applied(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "compat.py").write_text(
        "import sys\n\nPY3 = sys.version_info[0] == 3\n\ndef current():\n    return PY3\n",
        encoding="utf-8",
    )
    function = RepoParser().parse(root.resolve()).functions[0]
    regions, skipped = build_v3_editable_regions(
        root,
        {"rankings": [_ranking(function, 1, 1.0)]},
    )

    assert skipped == []
    assert len(regions) == 2
    region = next(region for region in regions if region.region_kind == "module")
    assert region.region_kind == "module"
    assert region.function_name == "__module__"
    replacement = (
        "import sys\n\n"
        "PY3 = sys.version_info[0] == 3\n"
        "PY2 = not PY3\n\n"
        "def current():\n"
        "    return PY3"
    )
    parsed = parse_v3_patch_response(
        json.dumps(
            {
                "files": [
                    {
                        "path": region.path,
                        "original_sha256": region.original_sha256,
                        "replacement": replacement,
                    }
                ],
                "risk": "low",
            }
        ),
        editable_regions=regions,
    )
    validation = validate_v3_patch_candidate(
        parsed["candidate"],
        editable_regions=regions,
        repository_root=root,
    )
    application = apply_v3_patch_candidate(
        parsed["candidate"],
        editable_regions=regions,
        repository_root=root,
    )

    assert parsed["status"] == "pass", parsed
    assert parsed["candidate"]["files"][0]["region_kind"] == "module"
    assert validation["status"] == "pass", validation
    assert validation["files"][0]["region_kind"] == "module"
    assert application["status"] == "pass", application
    assert (root / "compat.py").read_text(encoding="utf-8") == replacement + "\n"


def test_module_regions_exclude_examples_and_other_auxiliary_source(tmp_path):
    root = tmp_path / "repository"
    examples = root / "examples"
    examples.mkdir(parents=True)
    (root / "main.py").write_text(
        "def run():\n"
        "    import config\n"
        "    import examples.app\n"
        "    return config.ENABLED\n",
        encoding="utf-8",
    )
    (root / "config.py").write_text("ENABLED = True\n", encoding="utf-8")
    (examples / "app.py").write_text("ENABLED = False\n", encoding="utf-8")
    function = RepoParser().parse(root.resolve()).functions[0]

    regions, _ = build_v3_editable_regions(
        root,
        {"rankings": [_ranking(function, 1, 1.0)]},
    )

    module_paths = {
        region.path for region in regions if region.region_kind == "module"
    }
    assert "config.py" in module_paths
    assert "examples/app.py" not in module_paths


def test_editable_scope_expands_top_ranked_method_to_same_class_neighbors(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "worker.py").write_text(
        "class Worker:\n"
        "    def first(self, value):\n"
        "        return value\n\n"
        "    def second(self, value):\n"
        "        return value + 1\n\n"
        "    def third(self, value):\n"
        "        return value + 2\n",
        encoding="utf-8",
    )
    functions = {
        function.name: function
        for function in RepoParser().parse(root.resolve()).functions
    }

    regions, skipped = build_v3_editable_regions(
        root,
        {"rankings": [_ranking(functions["first"], 1, 0.9)]},
        top_k=1,
        max_regions=3,
    )

    assert skipped == []
    assert [region.function_name for region in regions] == [
        "Worker.first",
        "Worker.second",
        "Worker.third",
    ]
    assert regions[0].selection_reason == "top_k_localization"
    assert regions[1].selection_reason == "same_class_neighbor_of:Worker.first"


def test_large_repository_scope_follows_failing_test_local_imports(tmp_path):
    root = tmp_path / "repository"
    package = root / "package"
    tests = root / "tests"
    package.mkdir(parents=True)
    tests.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "target.py").write_text(
        "from package.helper import normalize\n\n"
        "def calculate(value):\n"
        "    return normalize(value)\n",
        encoding="utf-8",
    )
    (package / "helper.py").write_text(
        "def normalize(value):\n    return value\n",
        encoding="utf-8",
    )
    (package / "consumer.py").write_text(
        "from package.target import calculate\n\n"
        "def consume(value):\n"
        "    return calculate(value)\n",
        encoding="utf-8",
    )
    (tests / "test_target.py").write_text(
        "from package.target import calculate\n\n"
        "def test_calculate():\n"
        "    assert calculate(2) == 3\n",
        encoding="utf-8",
    )
    for index in range(8):
        (root / f"irrelevant_{index}.py").write_text(
            f"def unrelated_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    scope = select_v3_analysis_scope(
        root,
        case={"targeted_test_commands": [["pytest", "tests/test_target.py"]]},
        dynamic_evidence={
            "failing_tests": [
                {
                    "path": str(tests / "test_target.py"),
                    "test_name": "test_calculate",
                }
            ],
            "traceback_frames": [],
        },
        full_repository_file_threshold=3,
        scoped_file_limit=5,
        import_depth=2,
    )

    assert scope["status"] == "pass"
    assert scope["mode"] == "bounded_dynamic_import_scope"
    assert scope["ground_truth_used"] is False
    assert scope["selected_file_count"] <= 5
    assert "tests/test_target.py" in scope["seed_paths"]
    assert "package/target.py" in scope["import_expansion_paths"]
    assert "package/helper.py" in scope["import_expansion_paths"]
    assert "package/consumer.py" in scope["reverse_import_expansion_paths"]
    assert not any(path.startswith("irrelevant_") for path in scope["analysis_paths"])


def test_small_repository_scope_keeps_full_analysis(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")

    scope = select_v3_analysis_scope(
        root,
        case={},
        dynamic_evidence={},
        full_repository_file_threshold=2,
    )

    assert scope["status"] == "pass"
    assert scope["mode"] == "full_repository"
    assert scope["analysis_paths"] is None
    assert scope["selected_file_count"] == 1
    assert scope["ground_truth_used"] is False


def test_patch_response_rejects_unauthorized_path_and_wrong_region_hash(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "app.py").write_text(
        "def value():\n    return 1\n",
        encoding="utf-8",
    )
    function = RepoParser().parse(root.resolve()).functions[0]
    regions, _ = build_v3_editable_regions(
        root,
        {"rankings": [_ranking(function, 1, 1.0)]},
    )

    unauthorized = parse_v3_patch_response(
        json.dumps(
            {
                "files": [
                    {
                        "path": "tests/test_app.py",
                        "original_sha256": regions[0].original_sha256,
                        "replacement": "def test_app():\n    pass",
                    }
                ]
            }
        ),
        editable_regions=regions,
    )
    wrong_hash = parse_v3_patch_response(
        json.dumps(
            {
                "files": [
                    {
                        "path": "app.py",
                        "original_sha256": "0" * 64,
                        "replacement": "def value():\n    return 2",
                    }
                ]
            }
        ),
        editable_regions=regions,
    )

    assert unauthorized["status"] == "fail"
    assert unauthorized["errors"] == ["files[0].path_is_unsafe"]
    assert wrong_hash["status"] == "fail"
    assert wrong_hash["errors"] == ["files[0].region_not_authorized"]


def test_patch_safety_gate_blocks_signature_changes_and_duplicate_failed_patch(
    tmp_path,
):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "app.py").write_text(
        "def value(item):\n    return item\n",
        encoding="utf-8",
    )
    function = RepoParser().parse(root.resolve()).functions[0]
    regions, _ = build_v3_editable_regions(
        root,
        {"rankings": [_ranking(function, 1, 1.0)]},
    )
    parsed = parse_v3_patch_response(
        json.dumps(
            {
                "files": [
                    {
                        "path": "app.py",
                        "original_sha256": regions[0].original_sha256,
                        "replacement": "def value(item, extra=None):\n    return item",
                    }
                ]
            }
        ),
        editable_regions=regions,
    )

    first = validate_v3_patch_candidate(
        parsed["candidate"],
        editable_regions=regions,
        repository_root=root,
    )
    duplicate = validate_v3_patch_candidate(
        parsed["candidate"],
        editable_regions=regions,
        repository_root=root,
        failed_diff_fingerprints={first["combined_diff_fingerprint"]},
    )

    assert first["status"] == "blocked"
    assert "signature_changed" in first["reasons"]
    assert duplicate["status"] == "blocked"
    assert "duplicate_failed_combined_patch" in duplicate["reasons"]


def test_model_context_audit_detects_deliberate_fix_commit_leak(tmp_path):
    case = _case()
    context = {
        "editable_regions": [],
        "leaked_value": case["fix_commit_sha"],
    }

    audit = audit_v3_model_context(
        context,
        case=case,
        repository_root=tmp_path,
    )

    assert audit["status"] == "fail"
    assert "forbidden_value_present:fix_commit_sha" in audit["errors"]


def test_model_context_audit_detects_unsanitized_external_absolute_path(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    context = {
        "failure_evidence": {
            "failure_context": r"File C:\Users\someone\private\failure.log"
        },
        "localization": {},
        "editable_regions": [],
    }

    audit = audit_v3_model_context(
        context,
        case=_case(),
        repository_root=root,
    )

    assert audit["status"] == "fail"
    assert audit["contains_absolute_local_path"] is True
    assert "absolute_local_path_present" in audit["errors"]
    assert audit["absolute_local_path_locations"] == [
        "$.failure_evidence.failure_context"
    ]


def test_reflection_text_uses_the_same_absolute_path_redaction(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    value = (
        f"repo={root}\\app.py; "
        r"runtime=D:\Python\Lib\traceback.py; unix=/tmp/private.log"
    )

    sanitized = sanitize_v3_untrusted_text(
        value,
        repository_roots=[root],
        limit=1_000,
    )
    context = {
        "failure_evidence": {},
        "localization": {},
        "reflection": {"failure_context": sanitized},
        "editable_regions": [],
    }
    audit = audit_v3_model_context(
        context,
        case=_case(),
        repository_root=root,
    )

    assert str(root) not in sanitized
    assert r"D:\Python" not in sanitized
    assert "/tmp/private.log" not in sanitized
    assert sanitized.count("<external-path>") == 2
    assert audit["status"] == "pass", audit["errors"]


def test_failure_text_redacts_and_audits_relative_runtime_traversal(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    raw = (
        r"..\..\..\runtimes\cpython\lib\site-packages\requests\sessions.py:578"
    )

    sanitized = sanitize_v3_untrusted_text(
        raw,
        repository_roots=[root],
        limit=1_000,
    )
    safe_context = {
        "failure_evidence": {"failure_context": sanitized},
        "localization": {},
        "editable_regions": [],
    }
    unsafe_context = {
        "failure_evidence": {"failure_context": raw},
        "localization": {},
        "editable_regions": [],
    }

    safe_audit = audit_v3_model_context(
        safe_context,
        case=_case(),
        repository_root=root,
    )
    unsafe_audit = audit_v3_model_context(
        unsafe_context,
        case=_case(),
        repository_root=root,
    )

    assert sanitized == "<external-path>:578"
    assert safe_audit["status"] == "pass", safe_audit["errors"]
    assert unsafe_audit["status"] == "fail"
    assert "relative_traversal_path_present" in unsafe_audit["errors"]
    assert unsafe_audit["relative_traversal_path_locations"] == [
        "$.failure_evidence.failure_context"
    ]


def test_v3_repair_client_is_built_from_frozen_protocol():
    root = Path(__file__).resolve().parents[1]
    protocol = load_experiment_protocol(
        root / "datasets" / "v3_real_bugs" / "experiment_protocol.json"
    )

    client = create_v3_repair_client(
        protocol,
        root=root,
        prompt_id="patch_generation_v3",
        api_key="fake-key",
        sleeper=lambda delay: None,
    )
    transport = client.client

    assert transport.provider == "deepseek"
    assert transport.model == "deepseek-v4-pro"
    assert transport.temperature == 0
    assert transport.max_tokens == 32768
    assert transport.response_format == {"type": "json_object"}
    assert transport.thinking == "enabled"
    assert transport.reasoning_effort == "high"
    assert client.max_retries == 2
    assert client.backoff_seconds == (2.0, 8.0)


def test_v3_repair_client_accepts_secondary_protocol_key_environment(
    monkeypatch,
):
    root = Path(__file__).resolve().parents[1]
    protocol = load_experiment_protocol(
        root / "datasets" / "v3_real_bugs" / "experiment_protocol.json"
    )
    monkeypatch.delenv("CIA_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secondary-fixture-key")

    client = create_v3_repair_client(
        protocol,
        root=root,
        prompt_id="patch_generation_v3",
    )

    assert client.client.api_key == "secondary-fixture-key"
    assert client.client.api_key_fingerprint.startswith("sha256:")


def test_candidate_executes_targeted_and_full_regression_in_independent_workspace(
    tmp_path,
    monkeypatch,
):
    seed = tmp_path / "seed"
    tests = seed / "tests"
    tests.mkdir(parents=True)
    (seed / "calculator.py").write_text(
        "def ratio(total, count):\n    return total / count\n",
        encoding="utf-8",
    )
    (tests / "test_calculator.py").write_text(
        "from calculator import ratio\n\n"
        "def test_zero():\n"
        "    assert ratio(5, 0) == 0\n\n"
        "def test_nonzero():\n"
        "    assert ratio(6, 2) == 3\n",
        encoding="utf-8",
    )
    function = RepoParser().parse(seed.resolve()).functions[0]
    regions, _ = build_v3_editable_regions(
        seed,
        {"rankings": [_ranking(function, 1, 1.0)]},
    )
    response = parse_v3_patch_response(
        json.dumps(
            {
                "analysis": "Handle the observed zero denominator.",
                "files": [
                    {
                        "path": "calculator.py",
                        "original_sha256": regions[0].original_sha256,
                        "replacement": (
                            "def ratio(total, count):\n"
                            "    if count == 0:\n"
                            "        return 0\n"
                            "    return total / count"
                        ),
                    }
                ],
                "risk": "low",
            }
        ),
        editable_regions=regions,
    )
    case = _case()
    case["ground_truth"]["source_files"] = ["calculator.py"]
    case["targeted_test_commands"] = [
        [
            "{python}",
            "-m",
            "pytest",
            "-q",
            "tests/test_calculator.py::test_zero",
        ]
    ]
    case["regression_command"] = ["{python}", "-m", "pytest", "-q"]
    workspace = tmp_path / "trials" / "trial-1" / "candidate-1"

    execution = execute_v3_patch_candidate(
        response["candidate"],
        editable_regions=regions,
        seed_repository=seed,
        trial_workspace=workspace,
        case=case,
        python_executable=sys.executable,
        targeted_timeout=30,
        regression_timeout=30,
    )

    assert execution["outcome_status"] == "verified_repair", execution
    assert execution["validation"]["targeted_tests"] == "pass"
    assert execution["validation"]["full_regression"] == "pass"
    assert execution["validation"]["semantic_validation"] == "pass"
    semantic = execution["semantic"]
    assert semantic["claim_eligible"] is True
    assert semantic["gold_patch_used"] is False
    checks = {row["check_id"]: row for row in semantic["checks"]}
    assert checks["api_contract_compatibility"]["status"] == "pass"
    assert checks["patched_workspace_consistency"]["status"] == "pass"
    assert checks["target_behavior_differential"]["status"] == "pass"
    assert checks["target_behavior_differential"]["patched_execution_reused"] is True
    assert checks["reverse_mutation_sensitivity"]["status"] == "pass"
    assert checks["reverse_mutation_sensitivity"]["killed_mutation_count"] == 1
    assert execution["workspace"]["status"] == "pass"
    assert workspace.is_dir()
    assert "return 0" in (workspace / "calculator.py").read_text(encoding="utf-8")
    assert "return 0" not in (seed / "calculator.py").read_text(encoding="utf-8")

    monkeypatch.setattr(
        repair_execution,
        "validate_v3_semantic_candidate",
        lambda *args, **kwargs: {
            "status": "fail",
            "reason": "reverse_mutation_survived_complete_test_oracle",
            "claim_eligible": False,
            "checks": [],
        },
    )
    rejected = execute_v3_patch_candidate(
        response["candidate"],
        editable_regions=regions,
        seed_repository=seed,
        trial_workspace=tmp_path / "trials" / "trial-1" / "candidate-2",
        case=case,
        python_executable=sys.executable,
        targeted_timeout=30,
        regression_timeout=30,
    )
    assert rejected["validation"]["targeted_tests"] == "pass"
    assert rejected["validation"]["full_regression"] == "pass"
    assert rejected["validation"]["semantic_validation"] == "fail"
    assert rejected["outcome_status"] == "failed"
    assert rejected["failure_layer"] == "semantic_validation"


def test_trial_workspace_cannot_be_created_inside_seed_repository(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "app.py").write_text("value = 1\n", encoding="utf-8")

    result = copy_v3_trial_workspace(seed, seed / "nested-trial")

    assert result["status"] == "fail"
    assert result["reason"] == "trial_workspace_must_not_be_inside_seed_repository"
    assert not (seed / "nested-trial").exists()


def test_run_record_from_real_candidate_execution_is_protocol_valid(tmp_path):
    del tmp_path
    root = Path(__file__).resolve().parents[1]
    protocol = load_experiment_protocol(
        root / "datasets" / "v3_real_bugs" / "experiment_protocol.json"
    )
    execution = {
        "validation": {
            "ast_valid": True,
            "safety_gate": "pass",
            "targeted_tests": "pass",
            "full_regression": "pass",
            "semantic_validation": "pass",
            "semantic_justification": "All required semantic gates passed.",
            "semantic_validation_details": {
                "status": "pass",
                "claim_eligible": True,
                "checks": [],
            },
        },
        "outcome_status": "verified_repair",
        "failure_layer": "none",
        "failure_category": "none",
        "failure_reason": "",
        "validation_latency_ms": 125,
    }
    metadata = {
        "latency_ms": 250,
        "response_id": "response-test",
        "response_model": "deepseek-v4-pro",
        "finish_reason": "stop",
        "response_sha256": "a" * 64,
        "prompt_sha256": "sha256:" + "b" * 64,
        "system_prompt_sha256": "sha256:" + "c" * 64,
        "provider_retry_count": 1,
        "provider_retry_reasons": ["http_503"],
        "usage": {
            "source": "provider_usage",
            "prompt_tokens": 100,
            "prompt_cache_hit_tokens": 10,
            "prompt_cache_miss_tokens": 90,
            "completion_tokens": 20,
            "reasoning_tokens": 5,
        },
    }
    trial_id = "ab978e1c-3116-438d-aa41-dbfbeca3104e"

    record = build_v3_run_record(
        protocol,
        case=_case(),
        strategy_mode="llm",
        trial_index=1,
        trial_id=trial_id,
        candidate_index=1,
        candidate_id="case-001-llm-t1-c1",
        generator_family="llm",
        generator_id="deepseek_direct",
        reflection_round=0,
        parent_candidate_id="",
        prompt_id="patch_generation_v3",
        llm_metadata=metadata,
        execution=execution,
        model_context_artifact="contexts/case-001.json",
        artifacts={
            "patch": "patches/case-001.diff",
            "targeted_test": "tests/case-001-targeted.json",
            "full_regression": "tests/case-001-regression.json",
        },
        started_at="2026-07-15T00:00:00+00:00",
        completed_at="2026-07-15T00:00:01+00:00",
    )
    audit = validate_run_record(record, protocol=protocol)

    assert audit["status"] == "pass", audit["errors"]
    assert record["usage"]["total_tokens"] == 120
    assert record["cost"]["actual_cost_usd"] > 0
    assert record["timing"]["provider_retry_count"] == 1
    assert record["model"]["provider_response_model"] == "deepseek-v4-pro"
    assert record["model"]["finish_reason"] == "stop"
    assert record["model"]["request_prompt_sha256"] == "sha256:" + "b" * 64
    assert record["model"]["system_prompt_sha256"] == "sha256:" + "c" * 64


def test_provider_error_is_classified_separately_from_repair_failure():
    error = LLMRequestError(
        "http_error",
        "LLM request failed with HTTP 401.",
        {"http_status": 401, "provider_retry_count": 0},
    )

    execution = provider_blocker_execution(error)

    assert execution["outcome_status"] == "provider_blocker"
    assert execution["failure_layer"] == "provider"
    assert execution["failure_category"] == "authentication"
    assert execution["validation"]["targeted_tests"] == "not_run"


def test_llm_trial_runs_direct_failure_then_bounded_reflection_recovery(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    protocol = load_experiment_protocol(
        project_root / "datasets" / "v3_real_bugs" / "experiment_protocol.json"
    )
    seed = tmp_path / "seed"
    tests = seed / "tests"
    tests.mkdir(parents=True)
    (seed / "calculator.py").write_text(
        "def ratio(total, count):\n    return total / count\n",
        encoding="utf-8",
    )
    (tests / "test_calculator.py").write_text(
        "from calculator import ratio\n\n"
        "def test_zero():\n"
        "    assert ratio(5, 0) == 0\n\n"
        "def test_nonzero():\n"
        "    assert ratio(6, 2) == 3\n",
        encoding="utf-8",
    )
    case = _case()
    case["ground_truth"]["source_files"] = ["calculator.py"]
    case["targeted_test_commands"] = [
        [
            "{python}",
            "-m",
            "pytest",
            "-q",
            "tests/test_calculator.py::test_zero",
        ]
    ]
    case["regression_command"] = ["{python}", "-m", "pytest", "-q"]
    baseline_execution = {
        "status": "fail",
        "executed": True,
        "reason": "command_returncode",
        "command": "python -m pytest -q tests/test_calculator.py::test_zero",
        "returncode": 1,
        "passed": 0,
        "failed": 1,
        "failure_category": "test_assertion_failure",
        "failure_signal": "FAILED tests/test_calculator.py::test_zero",
        "diagnostic_summary": "The targeted assertion failed with ZeroDivisionError.",
        "failure_context": (
            "FAILED tests/test_calculator.py::test_zero - ZeroDivisionError\n"
            f'  File "{seed / "calculator.py"}", line 2, in ratio\n'
            "    return total / count"
        ),
    }
    prepared = prepare_v3_repair_case(
        case,
        seed_repository=seed,
        baseline_execution=baseline_execution,
        output_dir=tmp_path / "preparation",
    )
    assert prepared.analysis_scope["mode"] == "full_repository"
    assert prepared.model_context["analysis_scope"]["ground_truth_used"] is False
    assert prepared.model_context_audit["status"] == "pass"
    assert prepared.analysis_scope_ground_truth_audit["status"] == "pass"
    assert (
        prepared.analysis_scope_ground_truth_audit["ground_truth_used_for_selection"]
        is False
    )
    assert Path(prepared.preparation_artifacts["analysis_scope"]).is_file()
    assert Path(prepared.preparation_artifacts["model_context_audit"]).is_file()
    assert Path(
        prepared.preparation_artifacts["analysis_scope_ground_truth_audit"]
    ).is_file()
    region = next(
        region for region in prepared.editable_regions if region.function_name == "ratio"
    )
    direct_client = StaticLLMClient(
        json.dumps(
            {
                "analysis": "First hypothesis uses the wrong fallback.",
                "files": [
                    {
                        "path": region.path,
                        "original_sha256": region.original_sha256,
                        "replacement": (
                            "def ratio(total, count):\n"
                            "    if count == 0:\n"
                            "        return -1\n"
                            "    return total / count"
                        ),
                    }
                ],
                "risk": "low",
            }
        )
    )
    reflection_client = StaticLLMClient(
        json.dumps(
            {
                "failure_diagnosis": "The fallback did not match the observed oracle.",
                "change_from_parent": "Return the expected neutral value.",
                "files": [
                    {
                        "path": region.path,
                        "original_sha256": region.original_sha256,
                        "replacement": (
                            "def ratio(total, count):\n"
                            "    if count == 0:\n"
                            "        return 0\n"
                            "    return total / count"
                        ),
                    }
                ],
                "risk": "low",
            }
        )
    )

    result = run_v3_repair_trial(
        protocol,
        prepared,
        project_root=project_root,
        output_dir=tmp_path / "trial",
        strategy_mode="llm",
        trial_index=1,
        python_executable=sys.executable,
        llm_client=direct_client,
        reflection_client=reflection_client,
        targeted_timeout=30,
        regression_timeout=30,
    )
    records_audit = validate_run_records(
        result["records"],
        protocol=protocol,
        require_complete=False,
    )

    assert result["status"] == "pass", result
    assert result["verified_repair"] is True
    assert result["record_count"] == 2
    assert result["records"][0]["outcome"]["status"] == "failed"
    assert result["records"][0]["failure"]["layer"] == "targeted_test"
    assert result["records"][1]["outcome"]["status"] == "verified_repair"
    assert result["records"][1]["outcome"]["reflection_recovered"] is True
    assert result["records"][1]["candidate"]["parent_candidate_id"] == (
        result["records"][0]["candidate"]["candidate_id"]
    )
    assert records_audit["status"] == "pass", records_audit["errors"]
    assert len(direct_client.prompts) == 1
    assert len(reflection_client.prompts) == 1
    assert "failed_diff_fingerprints" in reflection_client.prompts[0]
    assert (tmp_path / "trial" / "workspaces" / "candidate-1").is_dir()
    assert (tmp_path / "trial" / "workspaces" / "candidate-2").is_dir()


def test_reproduction_seed_audit_verifies_commit_and_overlay_hash(tmp_path):
    reproduction_dir = tmp_path / "reproduction" / "case-001"
    seed = reproduction_dir / "bug" / "repository_checkout"
    tests = seed / "tests"
    tests.mkdir(parents=True)
    overlay = tests / "test_app.py"
    overlay.write_text("def test_app():\n    assert True\n", encoding="utf-8")
    overlay_hash = hashlib.sha256(overlay.read_bytes()).hexdigest()
    reproduction = {
        "status": "pass",
        "acceptance": {"reproducible": True},
        "preparation": {
            "bug_checkout": {"ref": "b" * 40},
            "test_overlay": {
                "files": [
                    {
                        "path": "tests/test_app.py",
                        "sha256": overlay_hash,
                    }
                ]
            },
            "bug_preparation_files": {"files": []},
        },
        "bug_targeted": {"results": []},
    }
    artifact = reproduction_dir / "reproduction.json"
    artifact.write_text(json.dumps(reproduction), encoding="utf-8")
    artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    case = _case()
    case["reproduction"] = {"evidence_sha256": artifact_hash}

    passed = audit_v3_reproduction_seed(case, reproduction_dir=reproduction_dir)
    overlay.write_text("changed\n", encoding="utf-8")
    failed = audit_v3_reproduction_seed(case, reproduction_dir=reproduction_dir)

    assert passed["status"] == "pass", passed["errors"]
    assert passed["fix_checkout_used_as_trial_seed"] is False
    assert failed["status"] == "fail"
    assert "seed_file_sha256_mismatch:tests/test_app.py" in failed["errors"]


def test_repair_metrics_compute_pass_at_k_and_keep_failure_denominators():
    root = Path(__file__).resolve().parents[1]
    protocol = load_experiment_protocol(
        root / "datasets" / "v3_real_bugs" / "experiment_protocol.json"
    )
    case_one = _case()
    case_two = _case()
    case_two["case_id"] = "case-002"
    records = [
        _evaluation_record(
            protocol,
            case=case_one,
            trial_index=1,
            verified=True,
            reflection_round=0,
        ),
        _evaluation_record(
            protocol,
            case=case_one,
            trial_index=2,
            verified=False,
            reflection_round=0,
        ),
        _evaluation_record(
            protocol,
            case=case_one,
            trial_index=3,
            verified=False,
            reflection_round=0,
        ),
        _evaluation_record(
            protocol,
            case=case_two,
            trial_index=1,
            verified=False,
            reflection_round=0,
        ),
        _evaluation_record(
            protocol,
            case=case_two,
            trial_index=2,
            verified=True,
            reflection_round=1,
        ),
        _evaluation_record(
            protocol,
            case=case_two,
            trial_index=3,
            verified=False,
            reflection_round=0,
        ),
    ]

    metrics = build_v3_repair_metrics(
        records,
        case_ids=["case-001", "case-002"],
        strategies=["llm"],
        protocol=protocol,
    )["llm"]
    completeness = audit_v3_evaluation_completeness(
        records,
        case_ids=["case-001", "case-002"],
        strategies=["llm"],
        protocol=protocol,
    )

    assert metrics["pass_at_1"] == 0.5
    assert metrics["pass_at_3"] == 1.0
    assert metrics["verified_repair_rate"] == 1.0
    assert metrics["reflection_recovery_rate"] == 0.5
    assert metrics["targeted_test_denominator"] == 6
    assert metrics["targeted_test_pass_rate"] == round(2 / 6, 6)
    assert completeness["status"] == "pass"
    assert completeness["expected_trial_count"] == 6


def test_repair_metrics_report_semantic_and_reverse_mutation_evidence():
    root = Path(__file__).resolve().parents[1]
    protocol = load_experiment_protocol(
        root / "datasets" / "v3_real_bugs" / "experiment_protocol.json"
    )
    record = _evaluation_record(
        protocol,
        case=_case(),
        trial_index=1,
        verified=True,
        reflection_round=0,
    )
    record["validation"]["semantic_validation"] = "pass"
    record["validation"]["semantic_validation_details"] = {
        "status": "pass",
        "claim_eligible": True,
        "checks": [
            {
                "check_id": "api_contract_compatibility",
                "status": "pass",
            },
            {
                "check_id": "patched_workspace_consistency",
                "status": "pass",
            },
            {
                "check_id": "patch_minimality",
                "status": "pass",
            },
            {
                "check_id": "target_behavior_differential",
                "status": "pass",
            },
            {
                "check_id": "generated_boundary_property_probe",
                "status": "pass",
                "probe_count": 1,
                "case_count": 3,
            },
            {
                "check_id": "manifest_semantic_commands",
                "status": "pass",
                "commands": [{"kind": "property", "status": "pass"}],
            },
            {
                "check_id": "reverse_mutation_sensitivity",
                "status": "pass",
                "mutation_count": 2,
                "killed_mutation_count": 2,
                "surviving_mutation_count": 0,
            },
        ],
    }
    blocked = copy.deepcopy(record)
    blocked["validation"]["semantic_validation"] = "blocker"
    blocked["validation"]["semantic_validation_details"] = {
        "status": "blocker",
        "claim_eligible": False,
        "checks": [],
    }

    metrics = build_v3_repair_metrics(
        [record, blocked],
        case_ids=["case-001"],
        strategies=["llm"],
        protocol=protocol,
    )["llm"]

    assert metrics["semantic_validation_pass_rate"] == 1.0
    assert metrics["semantic_claim_eligible_record_count"] == 1
    assert metrics["semantic_claim_eligible_rate"] == 0.5
    assert metrics["semantic_validation_attempted_denominator"] == 2
    assert metrics["semantic_validation_blocker_count"] == 1
    assert metrics["api_contract_pass_rate"] == 1.0
    assert metrics["workspace_consistency_pass_rate"] == 1.0
    assert metrics["patch_minimality_pass_rate"] == 1.0
    assert metrics["target_differential_pass_rate"] == 1.0
    assert metrics["generated_boundary_probe_count"] == 1
    assert metrics["generated_boundary_case_count"] == 3
    assert metrics["manifest_semantic_command_count"] == 1
    assert metrics["reverse_mutation_count"] == 2
    assert metrics["reverse_mutation_kill_rate"] == 1.0
    assert metrics["reverse_mutation_surviving_count"] == 0


def test_repair_evaluation_summarizes_model_and_prompt_hash_metadata():
    root = Path(__file__).resolve().parents[1]
    protocol = load_experiment_protocol(
        root / "datasets" / "v3_real_bugs" / "experiment_protocol.json"
    )
    record = _evaluation_record(
        protocol,
        case=_case(),
        trial_index=1,
        verified=False,
        reflection_round=0,
    )

    summary = summarize_v3_model_metadata([record], protocol)

    assert summary["status"] == "pass"
    assert summary["model_record_count"] == 1
    assert summary["missing_core_metadata_count"] == 0
    assert summary["protocol_provider"] == "deepseek"
    assert summary["protocol_model_id"] == "deepseek-v4-pro"
    assert "patch_generation_v3" in summary["protocol_prompt_hashes"]
    assert summary["observed_providers"] == ["deepseek"]
    assert summary["observed_model_ids"] == ["deepseek-v4-pro"]
    assert summary["raw_prompts_persisted"] is False
    assert summary["raw_provider_payloads_persisted"] is False


def test_resume_requires_the_current_trial_input_fingerprint(tmp_path):
    root = Path(__file__).resolve().parents[1]
    protocol = load_experiment_protocol(
        root / "datasets" / "v3_real_bugs" / "experiment_protocol.json"
    )
    repository = tmp_path / "repository"
    repository.mkdir()
    prepared = PreparedV3RepairCase(
        case=_case(),
        seed_repository=repository,
        dynamic_evidence={"failure": "observed"},
        analysis_scope={"analysis_paths": None},
        analysis_scope_ground_truth_audit={
            "selection_snapshot_sha256": "selection-a"
        },
        localization={"rankings": []},
        editable_regions=[],
        model_context={},
        model_context_audit={"context_sha256": "context-a"},
        model_context_artifact="",
        preparation_artifacts={},
    )
    fingerprint = build_v3_trial_input_fingerprint(protocol, prepared)
    trial_path = tmp_path / "latest.json"
    trial_path.write_text(
        json.dumps(
            {
                "input_fingerprint": fingerprint,
                "records": [
                    _evaluation_record(
                        protocol,
                        case=_case(),
                        trial_index=1,
                        verified=False,
                        reflection_round=0,
                    )
                ],
            }
        ),
        encoding="utf-8",
    )

    resumed = _load_resumable_trial(
        trial_path,
        protocol=protocol,
        expected_input_fingerprint=fingerprint,
        retry_blockers=False,
    )
    stale = _load_resumable_trial(
        trial_path,
        protocol=protocol,
        expected_input_fingerprint="different-input",
        retry_blockers=False,
    )

    assert resumed is not None
    assert stale is None


def test_v3_repair_cli_accepts_bounded_trial_workers():
    args = build_arg_parser().parse_args(
        ["outputs_v3/example", "--max-workers", "3"]
    )

    assert args.max_workers == 3


def test_live_trial_workers_execute_independent_trials_concurrently(
    tmp_path,
    monkeypatch,
):
    project_root = Path(__file__).resolve().parents[1]
    protocol_path = project_root / "datasets" / "v3_real_bugs" / "experiment_protocol.json"
    case = _case()
    case["status"] = "accepted"
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps({"catalog_sha256": "catalog", "cases": [case]}),
        encoding="utf-8",
    )
    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(json.dumps({"profiles": []}), encoding="utf-8")
    seed = tmp_path / "seed"
    seed.mkdir()
    reproduction = tmp_path / "reproduction.json"
    reproduction.write_text(
        json.dumps({"bug_targeted": {"results": [{"status": "fail"}]}}),
        encoding="utf-8",
    )
    prepared = PreparedV3RepairCase(
        case=case,
        seed_repository=seed,
        dynamic_evidence={"status": "pass"},
        analysis_scope={"mode": "full_repository", "analysis_paths": None},
        analysis_scope_ground_truth_audit={
            "status": "pass",
            "selection_snapshot_sha256": "selection",
            "analysis_scope_file_recall": 1.0,
            "editable_file_recall": 1.0,
        },
        localization={"status": "pass", "rankings": []},
        editable_regions=[],
        model_context={},
        model_context_audit={"status": "pass", "context_sha256": "context"},
        model_context_artifact="contexts/case.json",
        preparation_artifacts={},
    )
    barrier = threading.Barrier(3)
    worker_threads: set[int] = set()

    monkeypatch.setenv("CIA_LLM_API_KEY", "fixture-key")
    monkeypatch.setattr(
        repair_eval,
        "audit_v3_reproduction_seed",
        lambda *args, **kwargs: {
            "status": "pass",
            "seed_repository": str(seed),
            "reproduction_artifact": str(reproduction),
        },
    )
    monkeypatch.setattr(
        repair_eval,
        "resolve_v3_case_runtime",
        lambda *args, **kwargs: {
            "status": "pass",
            "python_executable": sys.executable,
        },
    )
    monkeypatch.setattr(
        repair_eval,
        "prepare_v3_repair_case",
        lambda *args, **kwargs: prepared,
    )

    def fake_trial(protocol, prepared_case, **kwargs):
        del prepared_case
        barrier.wait(timeout=5)
        worker_threads.add(threading.get_ident())
        trial_index = kwargs["trial_index"]
        record = _evaluation_record(
            protocol,
            case=case,
            trial_index=trial_index,
            verified=False,
            reflection_round=0,
        )
        return {
            "schema_version": "3.0",
            "strategy_mode": "llm",
            "trial_index": trial_index,
            "trial_id": str(uuid.uuid4()),
            "status": "fail",
            "verified_repair": False,
            "winning_run_id": "",
            "record_count": 1,
            "records": [record],
            "candidates": [],
        }

    monkeypatch.setattr(repair_eval, "run_v3_repair_trial", fake_trial)

    result = run_v3_repair_evaluation(
        project_root=project_root,
        protocol_path=protocol_path,
        catalog_path=catalog_path,
        environment_profiles_path=profiles_path,
        reproduction_root=tmp_path,
        output_dir=tmp_path / "evaluation",
        strategies=["llm"],
        live_model=True,
        resume=False,
        max_workers=3,
    )

    assert result["status"] == "pass", result
    assert result["completeness"]["observed_trial_count"] == 3
    assert result["metrics"]["llm"]["observed_trial_count"] == 3
    assert len(worker_threads) == 3


def _evaluation_record(
    protocol: dict,
    *,
    case: dict,
    trial_index: int,
    verified: bool,
    reflection_round: int,
) -> dict:
    validation = {
        "ast_valid": True,
        "safety_gate": "pass",
        "targeted_tests": "pass" if verified else "fail",
        "full_regression": "pass" if verified else "not_run",
        "semantic_validation": "pass" if verified else "not_run",
        "semantic_justification": (
            "All required semantic gates passed."
            if verified
            else "Targeted test failed."
        ),
    }
    if verified:
        validation["semantic_validation_details"] = {
            "status": "pass",
            "claim_eligible": True,
            "checks": [],
        }
    execution = {
        "validation": validation,
        "outcome_status": "verified_repair" if verified else "failed",
        "failure_layer": "none" if verified else "targeted_test",
        "failure_category": "none" if verified else "test_assertion_failure",
        "failure_reason": "" if verified else "targeted test failed",
        "validation_latency_ms": 10,
    }
    return build_v3_run_record(
        protocol,
        case=case,
        strategy_mode="llm",
        trial_index=trial_index,
        trial_id=str(uuid.uuid4()),
        candidate_index=1,
        candidate_id=f"{case['case_id']}-llm-t{trial_index}-c1",
        generator_family="llm",
        generator_id="llm_direct" if reflection_round == 0 else "llm_reflection",
        reflection_round=reflection_round,
        parent_candidate_id="parent" if reflection_round else "",
        prompt_id=(
            "patch_generation_v3" if reflection_round == 0 else "reflection_v3"
        ),
        llm_metadata={
            "latency_ms": 20,
            "provider_retry_count": 0,
            "provider_retry_reasons": [],
            "usage": {
                "source": "provider_usage",
                "prompt_tokens": 10,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 10,
                "completion_tokens": 5,
            },
        },
        execution=execution,
        model_context_artifact="contexts/case.json",
        artifacts={
            "patch": "patches/candidate.diff",
            "targeted_test": "tests/targeted.json",
            "full_regression": "tests/regression.json",
        },
        started_at="2026-07-15T00:00:00+00:00",
        completed_at="2026-07-15T00:00:01+00:00",
    )


def _ranking(function, rank: int, score: float) -> dict:
    return {
        "function_id": function.id,
        "function_name": function.metadata.get("qualified_name", function.name),
        "file_path": function.file_path,
        "start_line": function.start_line,
        "end_line": function.end_line,
        "rank": rank,
        "score": score,
        "reason": "dynamic evidence and graph proximity",
        "signals": {"graph": score, "traceback": 0.5},
    }


def _case() -> dict:
    return {
        "case_id": "case-001",
        "benchmark_split": "development",
        "repository": {"owner_repo": "owner/project"},
        "bug_commit_sha": "b" * 40,
        "fix_commit_sha": "f" * 40,
        "targeted_test_commands": [
            ["{python}", "-m", "pytest", "-q", "tests/test_app.py"]
        ],
        "ground_truth": {
            "patch_sha256": "a" * 64,
            "benchmark_patch_path": "hidden/gold.patch",
        },
    }
