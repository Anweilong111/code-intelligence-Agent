import hashlib
from pathlib import Path
import json
import tempfile

from code_intelligence_agent.agents.llm_client import (
    ALIBABA_BEST_JUDGE_MODEL,
    ALIBABA_DASHSCOPE_CHAT_COMPLETIONS_URL,
    DEEPSEEK_BEST_JUDGE_MODEL,
    DEEPSEEK_CHAT_COMPLETIONS_URL,
    JUDGE_SYSTEM_PROMPT,
    SequenceLLMClient,
    StaticLLMClient,
    create_alibaba_judge_client,
    create_judge_client,
    create_patch_client,
    llm_config_audit,
    llm_config_audits_for_modes,
)
from code_intelligence_agent.evaluation.benchmark_loader import BenchmarkCase
from code_intelligence_agent.evaluation.benchmark_runner import (
    BenchmarkCaseResult,
    BenchmarkReport,
    BenchmarkRunner,
)
from code_intelligence_agent.evaluation.llm_judge import LLMJudge, parse_judgment
from code_intelligence_agent.evaluation.llm_config_audit import (
    render_llm_config_audit_markdown,
)
from code_intelligence_agent.evaluation.report import render_benchmark_markdown
from code_intelligence_agent.evaluation.judge_cluster_mining import (
    benchmark_mining_suggestions,
    patch_judge_audit_rows,
    patch_judge_failure_clusters,
)
from code_intelligence_agent.evaluation.judge_reliability import (
    case_judge_reliability_report,
)
from code_intelligence_agent.evaluation.patch_judge_reliability import (
    patch_judge_reliability_report,
)
from code_intelligence_agent.search.beam_patch_search import BeamPatchSearch
from code_intelligence_agent.search.patch_judge import (
    LLMPatchJudge,
    PatchJudgment,
    calibrate_patch_judgment,
    parse_patch_judgment,
    patch_judge_payload,
)
from code_intelligence_agent.search.scoring import PatchScoreWeights
from code_intelligence_agent.core.models import ExecutionResult, PatchCandidate


def test_parse_judgment_accepts_code_fence_and_clamps_score():
    judgment = parse_judgment(
        "```json\n"
        + json.dumps(
            {
                "score": 1.25,
                "verdict": "PASS",
                "reason": "Top-1 localization and sandbox repair succeeded.",
            }
        )
        + "\n```"
    )

    assert judgment.score == 1.0
    assert judgment.verdict == "pass"
    assert judgment.reason.startswith("Top-1 localization")


def test_llm_judge_returns_fail_for_invalid_json():
    judge = LLMJudge(StaticLLMClient("not json"))

    judgment = judge.judge_case({"case_name": "bad_judge_output"})

    assert judgment.score == 0.0
    assert judgment.verdict == "fail"
    assert "Invalid LLM judge response" in judgment.reason


def test_parse_patch_judgment_accepts_legacy_verdicts_and_clamps_score():
    judgment = parse_patch_judgment(
        json.dumps(
            {
                "score": 1.4,
                "verdict": "pass",
                "reason": "Sandbox success and low risk.",
            }
        )
    )

    assert judgment.score == 1.0
    assert judgment.verdict == "prefer"
    assert judgment.reason.startswith("Sandbox success")


def test_alibaba_judge_client_defaults_to_best_qwen_model(monkeypatch):
    monkeypatch.setenv("CIA_JUDGE_API_KEY", "fake-key")
    monkeypatch.delenv("CIA_JUDGE_PROVIDER", raising=False)
    monkeypatch.delenv("CIA_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("CIA_JUDGE_BASE_URL", raising=False)

    client = create_alibaba_judge_client()

    assert client.provider == "alibaba"
    assert client.api_key == "fake-key"
    assert ALIBABA_BEST_JUDGE_MODEL == "qwen3-max-thinking"
    assert client.model == ALIBABA_BEST_JUDGE_MODEL
    assert client.base_url == ALIBABA_DASHSCOPE_CHAT_COMPLETIONS_URL
    assert client.system_prompt == JUDGE_SYSTEM_PROMPT


def test_judge_client_defaults_to_deepseek_v4_pro(monkeypatch):
    monkeypatch.setenv("CIA_JUDGE_API_KEY", "fake-key")
    monkeypatch.delenv("CIA_JUDGE_PROVIDER", raising=False)
    monkeypatch.delenv("CIA_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("CIA_JUDGE_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    client = create_judge_client()

    assert client.provider == "deepseek"
    assert client.api_key == "fake-key"
    assert DEEPSEEK_BEST_JUDGE_MODEL == "deepseek-v4-pro"
    assert client.model == DEEPSEEK_BEST_JUDGE_MODEL
    assert client.base_url == DEEPSEEK_CHAT_COMPLETIONS_URL
    assert client.system_prompt == JUDGE_SYSTEM_PROMPT


def test_deepseek_judge_client_accepts_api_key_alias_and_model_alias(monkeypatch):
    monkeypatch.delenv("CIA_JUDGE_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-deepseek-key")
    monkeypatch.setenv("CIA_JUDGE_PROVIDER", "deepseek")
    monkeypatch.setenv("CIA_JUDGE_MODEL", "deepseekv4PRO")
    monkeypatch.setenv("CIA_JUDGE_BASE_URL", "https://api.deepseek.com")

    client = create_judge_client()

    assert client.provider == "deepseek"
    assert client.api_key == "fake-deepseek-key"
    assert client.model == DEEPSEEK_BEST_JUDGE_MODEL
    assert client.base_url == DEEPSEEK_CHAT_COMPLETIONS_URL


def test_deepseek_clients_accept_role_specific_timeout_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-deepseek-key")
    monkeypatch.setenv("CIA_LLM_TIMEOUT", "180")
    monkeypatch.setenv("CIA_JUDGE_TIMEOUT", "240")
    monkeypatch.delenv("CIA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("CIA_JUDGE_API_KEY", raising=False)
    monkeypatch.delenv("CIA_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CIA_JUDGE_PROVIDER", raising=False)

    patch_client = create_patch_client()
    judge_client = create_judge_client()

    assert patch_client.timeout == 180
    assert judge_client.timeout == 240


def test_llm_config_audit_masks_keys_and_tracks_enabled_roles(monkeypatch):
    key = "fake-secret-value"
    monkeypatch.setenv("CIA_JUDGE_API_KEY", key)
    for env_name in (
        "CIA_JUDGE_PROVIDER",
        "CIA_JUDGE_MODEL",
        "CIA_JUDGE_BASE_URL",
        "CIA_LOCALIZATION_LLM_API_KEY",
        "CIA_LOCALIZATION_LLM_PROVIDER",
        "CIA_LOCALIZATION_LLM_MODEL",
        "CIA_LOCALIZATION_LLM_BASE_URL",
        "DASHSCOPE_API_KEY",
        "ALIBABA_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)

    audit = llm_config_audit("judge", enabled=True)
    payload = audit.to_dict()
    serialized = json.dumps(payload)

    assert payload["provider"] == "deepseek"
    assert payload["model"] == DEEPSEEK_BEST_JUDGE_MODEL
    assert payload["base_url"] == DEEPSEEK_CHAT_COMPLETIONS_URL
    assert payload["api_key_present"] is True
    assert payload["api_key_source"] == "CIA_JUDGE_API_KEY"
    assert payload["api_key_fingerprint"] == (
        "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    )
    assert key not in serialized

    suite_audit = llm_config_audits_for_modes(
        patch_mode="rule",
        judge_mode="llm",
        patch_judge_mode="llm",
        llm_score_mode="llm",
    )
    roles = {item["role"]: item for item in suite_audit["roles"]}
    markdown = render_llm_config_audit_markdown(suite_audit)

    assert suite_audit["enabled_roles"] == ["judge", "localization"]
    assert suite_audit["configuration_complete"] is True
    assert roles["localization"]["api_key_source"] == "CIA_JUDGE_API_KEY"
    assert "deepseek-v4-pro" in markdown
    assert "sha256:" in markdown
    assert key not in markdown


def test_patch_generation_audit_defaults_to_deepseek_without_key(monkeypatch):
    for env_name in (
        "CIA_LLM_API_KEY",
        "CIA_LLM_PROVIDER",
        "CIA_LLM_MODEL",
        "CIA_LLM_BASE_URL",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)

    audit = llm_config_audit("patch_generation", enabled=True).to_dict()

    assert audit["provider"] == "deepseek"
    assert audit["model"] == DEEPSEEK_BEST_JUDGE_MODEL
    assert audit["base_url"] == DEEPSEEK_CHAT_COMPLETIONS_URL
    assert audit["api_key_present"] is False
    assert audit["warnings"] == ["missing_api_key:CIA_LLM_API_KEY"]


def test_llm_config_audit_treats_hybrid_patch_mode_as_llm_enabled(monkeypatch):
    for env_name in (
        "CIA_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "ALIBABA_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)

    audit = llm_config_audits_for_modes(
        patch_mode="hybrid",
        judge_mode="none",
        patch_judge_mode="none",
        llm_score_mode="none",
    )
    roles = {item["role"]: item for item in audit["roles"]}

    assert audit["enabled_roles"] == ["patch_generation"]
    assert audit["configuration_complete"] is False
    assert audit["missing_enabled_api_key_roles"] == ["patch_generation"]
    assert roles["patch_generation"]["enabled"] is True
    assert roles["patch_generation"]["api_key_present"] is False


def test_patch_judge_payload_omits_source_and_raw_diff():
    candidate = _judge_candidate("candidate_a")
    result = ExecutionResult(
        success=False,
        returncode=1,
        stdout="F",
        stderr="AssertionError",
        traceback="Traceback",
        passed=0,
        failed=1,
        timeout=False,
        command=[],
    )

    payload = patch_judge_payload(
        candidate=candidate,
        execution_result=result,
        localization_confidence=0.7,
        patch_risk=0.2,
    )
    serialized = json.dumps(payload)

    assert payload["candidate_id"] == "candidate_a"
    assert payload["diff_size"] > 0
    assert "old_source" not in serialized
    assert "new_source" not in serialized
    assert "def f" not in serialized
    assert "+    return 2" not in serialized


def test_patch_judge_calibration_caps_hard_failures():
    candidate = _judge_candidate(
        "syntax_candidate",
        metadata={
            "validation": {"valid": False, "scope_limited": False},
            "execution_feedback": {
                "failure_type": "syntax_error",
                "score": 0.05,
            },
        },
    )
    result = ExecutionResult(
        success=False,
        returncode=1,
        stdout="",
        stderr="SyntaxError: invalid syntax",
        traceback="",
        passed=0,
        failed=0,
        timeout=False,
        command=[],
    )

    calibrated = calibrate_patch_judgment(
        PatchJudgment(score=0.98, verdict="prefer", reason="Looks plausible."),
        candidate=candidate,
        execution_result=result,
        patch_risk=0.9,
    )

    assert calibrated.score == 0.98
    assert calibrated.calibrated_score == 0.4
    assert calibrated.agreement == "judge_more_optimistic"
    assert "capped_by_execution_evidence=0.40" in calibrated.calibration_reasons


def test_beam_patch_search_uses_patch_judge_to_rerank_candidates():
    low = _judge_candidate("low_judge", replacement=2)
    high = _judge_candidate("high_judge", replacement=3)
    client = SequenceLLMClient(
        [
            json.dumps(
                {
                    "score": 0.1,
                    "verdict": "reject",
                    "reason": "Weak evidence.",
                }
            ),
            json.dumps(
                {
                    "score": 0.95,
                    "verdict": "prefer",
                    "reason": "Better evidence.",
                }
            ),
        ]
    )
    zero_weights = PatchScoreWeights(
        tests_passed=0.0,
        localization=0.0,
        static_check=0.0,
        execution_feedback=0.0,
        diff_penalty=0.0,
        risk_penalty=0.0,
        warning_penalty=0.0,
        success_bonus=0.0,
    )

    results = BeamPatchSearch(
        sandbox=JudgeSandbox(success_ids={"high_judge"}),
        beam_width=2,
        max_depth=0,
        use_prior_ranking=False,
        patch_score_weights=zero_weights,
        patch_judge=LLMPatchJudge(client),
        patch_judge_weight=1.0,
    ).search(Path("."), [low, high])

    assert results[0].candidate.id == "high_judge"
    assert results[0].score >= 0.55
    assert results[0].candidate.metadata["patch_judgment"]["score"] == 0.95
    assert (
        results[0].candidate.metadata["patch_judgment"]["calibrated_score"]
        >= 0.55
    )
    assert results[0].candidate.metadata["patch_judgment"]["verdict"] == "prefer"
    assert results[1].candidate.metadata["patch_judgment"]["verdict"] == "reject"
    assert results[1].candidate.metadata["patch_judgment"]["agreement"]
    assert len(client.prompts) == 2
    assert "PATCH_CANDIDATE_EVIDENCE" in client.prompts[0]
    assert "def f" not in client.prompts[0]
    assert "+    return 2" not in client.prompts[0]


def test_benchmark_runner_records_llm_judgment_without_source_upload():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values)):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import shift_left\n\n"
            "def test_shift_left():\n"
            "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n",
            encoding="utf-8",
        )
        client = StaticLLMClient(
            json.dumps(
                {
                    "score": 0.95,
                    "verdict": "pass",
                    "reason": "Top-1 localization and validated patch.",
                }
            )
        )
        report = BenchmarkRunner(
            judge=LLMJudge(client),
            use_dynamic_coverage=False,
        ).run_cases(
            [
                BenchmarkCase(
                    name="judge_case",
                    repo_path=str(repo),
                    buggy_functions=["shift_left"],
                    expected_rule_ids=["possible_index_overrun"],
                    failing_tests=["test_shift_left"],
                    passed_tests=[],
                    test_args=[],
                    metadata={"bug_type": "boundary"},
                )
            ]
        )

    judgment = report.cases[0].llm_judgment
    assert judgment is not None
    assert judgment["score"] == 0.95
    assert judgment["verdict"] == "pass"
    assert "patch_success" in client.prompts[0]
    assert "def shift_left" not in client.prompts[0]

    payload = report.to_dict()
    assert payload["cases"][0]["llm_judgment"]["reason"] == (
        "Top-1 localization and validated patch."
    )
    assert "## LLM Judge Results" in render_benchmark_markdown(report)


def test_benchmark_runner_records_patch_judgment_in_beam_results():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "sample.py").write_text(
            "def shift_left(values):\n"
            "    for i in range(len(values)):\n"
            "        values[i] = values[i + 1]\n"
            "    return values\n",
            encoding="utf-8",
        )
        (repo / "test_sample.py").write_text(
            "from sample import shift_left\n\n"
            "def test_shift_left():\n"
            "    assert shift_left([1, 2, 3])[:2] == [2, 3]\n",
            encoding="utf-8",
        )
        client = StaticLLMClient(
            json.dumps(
                {
                    "score": 0.82,
                    "verdict": "accept",
                    "reason": "Plausible low-risk patch evidence.",
                }
            )
        )

        report = BenchmarkRunner(
            patch_judge=LLMPatchJudge(client),
            use_dynamic_coverage=False,
        ).run_cases(
            [
                BenchmarkCase(
                    name="patch_judge_case",
                    repo_path=str(repo),
                    buggy_functions=["shift_left"],
                    expected_rule_ids=["possible_index_overrun"],
                    failing_tests=["test_shift_left"],
                    passed_tests=[],
                    test_args=[],
                    metadata={"bug_type": "boundary"},
                )
            ]
        )

    judgment = report.cases[0].beam_search_results[0]["patch_judgment"]
    assert judgment["score"] == 0.82
    assert judgment["verdict"] == "accept"
    assert "def shift_left" not in client.prompts[0]
    markdown = render_benchmark_markdown(report)
    assert "## Patch Judge Audit" in markdown
    assert "## Patch Judge Reliability" in markdown
    assert "Judged Candidates" in markdown
    assert "Average Calibrated" in markdown
    assert "passed_ratio=" in markdown
    payload = report.to_dict()
    reliability = payload["summary"]["patch_judge_reliability"]
    assert reliability["judged_candidate_count"] >= 1
    assert "brier_score" in reliability


def test_patch_judge_audit_clusters_failed_judged_candidates():
    markdown = render_benchmark_markdown(_failed_patch_judge_report())

    assert "## Patch Judge Failure Clusters" in markdown
    assert "## Patch Judge Benchmark Mining" in markdown
    assert "syntax_error" in markdown
    assert "hard_failure" in markdown
    assert "judge_more_optimistic" in markdown
    assert "capped_by_execution_evidence" in markdown
    assert "judge false-positive hardening" in markdown
    assert "Add cases with attractive but non-executable decoy patches" in markdown
    assert "cluster_case#1:bad_patch" in markdown


def test_patch_judge_cluster_mining_returns_structured_suggestions():
    rows = patch_judge_audit_rows(_failed_patch_judge_report())
    clusters = patch_judge_failure_clusters(rows)
    suggestions = benchmark_mining_suggestions(clusters)

    assert rows[0].case == "cluster_case"
    assert rows[0].failure_type == "syntax_error"
    assert clusters[0].pattern == "capped_by_execution_evidence"
    assert clusters[0].examples == ["cluster_case#1:bad_patch"]
    assert suggestions[0].priority == "high"
    assert suggestions[0].benchmark_focus == "judge false-positive hardening"
    assert suggestions[0].evidence_count == 1
    assert "non-executable decoy patches" in suggestions[0].suggested_case_shape
    assert suggestions[0].to_dict()["failure_type"] == "syntax_error"


def test_case_level_llm_judge_reliability_reports_calibration_metrics():
    report = BenchmarkReport(
        cases=[
            _case_judge_result(
                case_name="aligned_success",
                top1_hit=True,
                top3_hit=True,
                patch_success=True,
                judge_score=0.90,
                verdict="pass",
                risk_score=0.10,
            ),
            _case_judge_result(
                case_name="false_positive",
                top1_hit=False,
                top3_hit=False,
                patch_success=False,
                judge_score=0.80,
                verdict="pass",
                expected_rule_recall=0.0,
            ),
            _case_judge_result(
                case_name="too_conservative",
                top1_hit=True,
                top3_hit=True,
                patch_success=True,
                judge_score=0.20,
                verdict="fail",
                risk_score=0.20,
            ),
        ]
    )

    reliability = case_judge_reliability_report(report, bin_count=5)

    assert reliability.judged_case_count == 3
    assert reliability.positive_case_count == 2
    assert reliability.agreement_counts == {
        "aligned": 1,
        "judge_more_conservative": 1,
        "judge_more_optimistic": 1,
    }
    assert reliability.verdict_counts == {"fail": 1, "pass": 2}
    assert reliability.brier_score == 0.43
    assert reliability.expected_calibration_error == 0.5
    assert reliability.rows[1].agreement == "judge_more_optimistic"
    assert reliability.rows[2].agreement == "judge_more_conservative"

    markdown = render_benchmark_markdown(report)
    assert "## LLM Judge Reliability" in markdown
    assert "Brier Score" in markdown
    assert "Expected Calibration Error" in markdown
    assert "judge_more_optimistic" in markdown

    payload = report.to_dict()
    summary = payload["summary"]["llm_judge_reliability"]
    assert summary["brier_score"] == 0.43
    assert summary["expected_calibration_error"] == (
        case_judge_reliability_report(report).expected_calibration_error
    )


def test_patch_judge_reliability_reports_candidate_calibration_metrics():
    report = _patch_judge_reliability_fixture()

    reliability = patch_judge_reliability_report(report, bin_count=5)

    assert reliability.judged_candidate_count == 3
    assert reliability.successful_candidate_count == 2
    assert reliability.agreement_counts == {
        "aligned": 1,
        "judge_more_conservative": 1,
        "judge_more_optimistic": 1,
    }
    assert reliability.verdict_counts == {"prefer": 3}
    assert reliability.failure_type_counts == {"success": 2, "test_failure": 1}
    assert reliability.brier_score == 0.43
    assert reliability.expected_calibration_error == 0.5
    assert reliability.rows[1].agreement == "judge_more_optimistic"
    assert reliability.rows[2].agreement == "judge_more_conservative"

    markdown = render_benchmark_markdown(report)
    assert "## Patch Judge Reliability" in markdown
    assert "Expected Calibration Error" in markdown
    assert "judge_more_optimistic" in markdown

    payload = report.to_dict()
    summary = payload["summary"]["patch_judge_reliability"]
    assert summary["brier_score"] == 0.43
    assert summary["expected_calibration_error"] == (
        patch_judge_reliability_report(report).expected_calibration_error
    )


class JudgeSandbox:
    def __init__(self, success_ids: set[str] | None = None) -> None:
        self.success_ids = success_ids or set()

    def apply_patch_and_test(
        self,
        repo_path,
        candidate: PatchCandidate,
        test_args: list[str] | None = None,
    ) -> ExecutionResult:
        del repo_path, test_args
        success = candidate.id in self.success_ids
        return ExecutionResult(
            success=success,
            returncode=0 if success else 1,
            stdout="." if success else "F",
            stderr="" if success else "AssertionError",
            traceback="Traceback",
            passed=1 if success else 0,
            failed=0 if success else 1,
            timeout=False,
            command=[],
        )


def _judge_candidate(
    candidate_id: str,
    metadata: dict | None = None,
    replacement: int = 2,
) -> PatchCandidate:
    return PatchCandidate(
        id=candidate_id,
        target_file="sample.py",
        relative_file_path="sample.py",
        target_function_id="sample.py::f",
        target_function_name="f",
        rule_id="test_rule",
        description="test patch candidate",
        old_source="def f():\n    return 1\n",
        new_source=f"def f():\n    return {replacement}\n",
        diff=(
            "--- a/sample.py\n"
            "+++ b/sample.py\n"
            "-    return 1\n"
            f"+    return {replacement}\n"
        ),
        metadata=metadata or {
            "variant": candidate_id,
            "validation": {"valid": True, "scope_limited": True},
        },
    )


def _case_judge_result(
    case_name: str,
    top1_hit: bool,
    top3_hit: bool,
    patch_success: bool,
    judge_score: float,
    verdict: str,
    expected_rule_recall: float = 1.0,
    risk_score: float | None = None,
) -> BenchmarkCaseResult:
    return BenchmarkCaseResult(
        case_name=case_name,
        bug_type="boundary",
        ranked_functions=["sample.py::target"],
        ground_truth={"sample.py::target"} if top1_hit else {"sample.py::other"},
        top1_hit=top1_hit,
        top3_hit=top3_hit,
        mrr=1.0 if top1_hit else 0.0,
        average_precision=1.0 if top1_hit else 0.0,
        ndcg_at_3=1.0 if top3_hit else 0.0,
        exam_score=0.0 if top1_hit else 1.0,
        findings_count=1,
        patch_candidates_count=1,
        expected_rule_ids=["possible_index_overrun"],
        detected_rule_ids=(
            ["possible_index_overrun"] if expected_rule_recall >= 1.0 else []
        ),
        expected_rule_recall=expected_rule_recall,
        expected_rule_precision=1.0 if expected_rule_recall >= 1.0 else 0.0,
        extra_rule_ids=[],
        coverage_mode="manifest",
        localization_details=[],
        patch_success=patch_success,
        repair_rounds=1,
        repair_strategy="beam_search",
        repair_results=[],
        best_patch_rule_id="possible_index_overrun" if patch_success else None,
        best_patch_risk=(
            {"score": risk_score}
            if risk_score is not None
            else None
        ),
        multi_patch_success=False,
        multi_patch_bundle_size=0,
        multi_patch_rules=[],
        multi_patch_results=[],
        patch_search_results=[],
        beam_search_results=[],
        search_analysis={},
        hypothesis_results=[],
        hypothesis_top1_hit=top1_hit,
        hypothesis_mrr=1.0 if top1_hit else 0.0,
        hypothesis_average_precision=1.0 if top1_hit else 0.0,
        hypothesis_ndcg_at_3=1.0 if top3_hit else 0.0,
        hypothesis_exam_score=0.0 if top1_hit else 1.0,
        llm_judgment={
            "score": judge_score,
            "verdict": verdict,
            "reason": f"{case_name} judge reason",
            "model": "mock-judge",
        },
    )


def _failed_patch_judge_report() -> BenchmarkReport:
    return BenchmarkReport(
        cases=[
            BenchmarkCaseResult(
                case_name="cluster_case",
                bug_type="boundary",
                ranked_functions=[],
                ground_truth=set(),
                top1_hit=False,
                top3_hit=False,
                mrr=0.0,
                average_precision=0.0,
                ndcg_at_3=0.0,
                exam_score=1.0,
                findings_count=0,
                patch_candidates_count=1,
                expected_rule_ids=[],
                detected_rule_ids=[],
                expected_rule_recall=0.0,
                expected_rule_precision=0.0,
                extra_rule_ids=[],
                coverage_mode="manifest",
                localization_details=[],
                patch_success=False,
                repair_rounds=1,
                repair_strategy="beam_search",
                repair_results=[],
                best_patch_rule_id=None,
                best_patch_risk=None,
                multi_patch_success=False,
                multi_patch_bundle_size=0,
                multi_patch_rules=[],
                multi_patch_results=[],
                patch_search_results=[],
                beam_search_results=[
                    {
                        "rank": 1,
                        "candidate_id": "bad_patch",
                        "parent_id": None,
                        "variant": "syntax_variant",
                        "rule_id": "test_rule",
                        "depth": 0,
                        "child_index": None,
                        "sibling_count": None,
                        "prior_score": 0.0,
                        "score": 0.4,
                        "feedback_score": 0.05,
                        "patch_judgment": {
                            "score": 0.98,
                            "calibrated_score": 0.4,
                            "agreement": "judge_more_optimistic",
                            "verdict": "prefer",
                            "reason": "Looks plausible.",
                            "calibration_reasons": [
                                "passed_ratio=0.00",
                                "patch_risk=0.90",
                                "failure_type=syntax_error",
                                "capped_by_execution_evidence=0.40",
                            ],
                        },
                        "retained": True,
                        "retention_bucket": "hard_failure",
                        "retention_reason": "syntax_error is a low-value refinement seed",
                        "success": False,
                        "risk_score": 0.9,
                        "risk": {"score": 0.9},
                        "passed": 0,
                        "failed": 0,
                        "failure_type": "syntax_error",
                        "failure_reason": "SyntaxError: invalid syntax",
                        "trace": [],
                    }
                ],
                search_analysis={},
                hypothesis_results=[],
                hypothesis_top1_hit=False,
                hypothesis_mrr=0.0,
                hypothesis_average_precision=0.0,
                hypothesis_ndcg_at_3=0.0,
                hypothesis_exam_score=1.0,
            )
        ]
    )


def _patch_judge_reliability_fixture() -> BenchmarkReport:
    case = BenchmarkCaseResult(
        case_name="patch_reliability_case",
        bug_type="boundary",
        ranked_functions=[],
        ground_truth=set(),
        top1_hit=False,
        top3_hit=False,
        mrr=0.0,
        average_precision=0.0,
        ndcg_at_3=0.0,
        exam_score=1.0,
        findings_count=0,
        patch_candidates_count=3,
        expected_rule_ids=[],
        detected_rule_ids=[],
        expected_rule_recall=0.0,
        expected_rule_precision=0.0,
        extra_rule_ids=[],
        coverage_mode="manifest",
        localization_details=[],
        patch_success=True,
        repair_rounds=1,
        repair_strategy="beam_search",
        repair_results=[],
        best_patch_rule_id=None,
        best_patch_risk=None,
        multi_patch_success=False,
        multi_patch_bundle_size=0,
        multi_patch_rules=[],
        multi_patch_results=[],
        patch_search_results=[],
        beam_search_results=[
            _patch_judge_beam_result(
                rank=1,
                candidate_id="aligned_success",
                success=True,
                raw_score=0.85,
                calibrated_score=0.90,
                passed=1,
                failed=0,
                failure_type="success",
            ),
            _patch_judge_beam_result(
                rank=2,
                candidate_id="false_positive",
                success=False,
                raw_score=0.95,
                calibrated_score=0.80,
                passed=0,
                failed=1,
                feedback_score=0.0,
                risk_score=0.90,
                failure_type="test_failure",
            ),
            _patch_judge_beam_result(
                rank=3,
                candidate_id="too_conservative",
                success=True,
                raw_score=0.30,
                calibrated_score=0.20,
                passed=1,
                failed=0,
                failure_type="success",
            ),
        ],
        search_analysis={},
        hypothesis_results=[],
        hypothesis_top1_hit=False,
        hypothesis_mrr=0.0,
        hypothesis_average_precision=0.0,
        hypothesis_ndcg_at_3=0.0,
        hypothesis_exam_score=1.0,
    )
    return BenchmarkReport(cases=[case])


def _patch_judge_beam_result(
    *,
    rank: int,
    candidate_id: str,
    success: bool,
    raw_score: float,
    calibrated_score: float,
    passed: int,
    failed: int,
    failure_type: str,
    feedback_score: float = 0.0,
    risk_score: float = 0.0,
) -> dict:
    return {
        "rank": rank,
        "candidate_id": candidate_id,
        "parent_id": None,
        "variant": candidate_id,
        "rule_id": "test_rule",
        "depth": 0,
        "child_index": None,
        "sibling_count": None,
        "prior_score": 0.0,
        "score": calibrated_score,
        "feedback_score": feedback_score,
        "patch_judgment": {
            "score": raw_score,
            "calibrated_score": calibrated_score,
            "agreement": "mock",
            "verdict": "prefer",
            "reason": f"{candidate_id} reason",
            "calibration_reasons": [],
        },
        "retained": True,
        "retention_bucket": "success" if success else "near_miss",
        "retention_reason": "",
        "success": success,
        "risk_score": risk_score,
        "risk": {"score": risk_score},
        "passed": passed,
        "failed": failed,
        "failure_type": failure_type,
        "failure_reason": "",
        "trace": [],
    }
