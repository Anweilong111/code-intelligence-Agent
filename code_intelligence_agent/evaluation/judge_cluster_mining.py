from __future__ import annotations

from dataclasses import asdict, dataclass

from code_intelligence_agent.evaluation.benchmark_runner import BenchmarkReport


@dataclass(frozen=True)
class PatchJudgeAuditRow:
    case: str
    rank: int
    candidate_id: str
    success: bool
    failure_type: str
    bucket: str
    raw_score: float
    calibrated_score: float
    delta: float
    agreement: str
    verdict: str
    reason_list: list[str]
    reasons: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PatchJudgeFailureCluster:
    failure_type: str
    bucket: str
    agreement: str
    pattern: str
    count: int
    average_raw: float
    average_calibrated: float
    average_delta: float
    examples: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkMiningSuggestion:
    priority: str
    benchmark_focus: str
    failure_type: str
    pattern: str
    suggested_case_shape: str
    rationale: str
    evidence_count: int
    examples: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def patch_judge_audit_rows(report: BenchmarkReport) -> list[PatchJudgeAuditRow]:
    rows = []
    for case in report.cases:
        for result in case.beam_search_results:
            judgment = result.get("patch_judgment", {})
            if not isinstance(judgment, dict) or "score" not in judgment:
                continue
            raw_score = float(judgment.get("score", 0.0))
            calibrated_score = float(
                judgment.get("calibrated_score", raw_score) or 0.0
            )
            reasons = judgment.get("calibration_reasons", [])
            if not isinstance(reasons, list):
                reasons = []
            rows.append(
                PatchJudgeAuditRow(
                    case=case.case_name,
                    rank=int(result.get("rank", 0)),
                    candidate_id=str(result.get("candidate_id", "")),
                    success=bool(result.get("success", False)),
                    failure_type=str(result.get("failure_type", "")),
                    bucket=str(result.get("retention_bucket", "")),
                    raw_score=raw_score,
                    calibrated_score=calibrated_score,
                    delta=calibrated_score - raw_score,
                    agreement=str(judgment.get("agreement", "")),
                    verdict=str(judgment.get("verdict", "")),
                    reason_list=[str(reason) for reason in reasons],
                    reasons=", ".join(str(reason) for reason in reasons),
                )
            )
    return rows


def patch_judge_failure_clusters(
    rows: list[PatchJudgeAuditRow],
) -> list[PatchJudgeFailureCluster]:
    clusters: dict[tuple[str, str, str, str], list[PatchJudgeAuditRow]] = {}
    for row in rows:
        if row.success is True and row.agreement == "aligned":
            continue
        key = (
            row.failure_type or "unknown",
            row.bucket or "unknown",
            row.agreement or "unknown",
            calibration_pattern(row),
        )
        clusters.setdefault(key, []).append(row)
    output = []
    for (failure_type, bucket, agreement, pattern), items in clusters.items():
        count = len(items)
        output.append(
            PatchJudgeFailureCluster(
                failure_type=failure_type,
                bucket=bucket,
                agreement=agreement,
                pattern=pattern,
                count=count,
                average_raw=sum(item.raw_score for item in items) / count,
                average_calibrated=(
                    sum(item.calibrated_score for item in items) / count
                ),
                average_delta=sum(item.delta for item in items) / count,
                examples=[
                    f"{item.case}#{item.rank}:{item.candidate_id}"
                    for item in items[:3]
                ],
            )
        )
    return sorted(
        output,
        key=lambda item: (
            item.count,
            abs(item.average_delta),
            item.failure_type,
        ),
        reverse=True,
    )


def benchmark_mining_suggestions(
    clusters: list[PatchJudgeFailureCluster],
) -> list[BenchmarkMiningSuggestion]:
    suggestions = [
        _suggestion_for_cluster(cluster)
        for cluster in clusters
    ]
    return sorted(
        suggestions,
        key=lambda item: (
            _priority_rank(item.priority),
            item.evidence_count,
            item.benchmark_focus,
        ),
        reverse=True,
    )


def calibration_pattern(row: PatchJudgeAuditRow) -> str:
    for reason in row.reason_list:
        if str(reason).startswith("capped_by_execution_evidence"):
            return "capped_by_execution_evidence"
    for reason in row.reason_list:
        if str(reason).startswith("raised_by_sandbox_success_floor"):
            return "raised_by_sandbox_success_floor"
    for reason in row.reason_list:
        if str(reason).startswith("failure_type="):
            return str(reason)
    if row.reason_list:
        return str(row.reason_list[0])
    return "unknown"


def _suggestion_for_cluster(
    cluster: PatchJudgeFailureCluster,
) -> BenchmarkMiningSuggestion:
    priority = _priority(cluster)
    focus = _benchmark_focus(cluster)
    case_shape = _suggested_case_shape(cluster)
    rationale = (
        f"{cluster.count} judged candidates share failure_type={cluster.failure_type}, "
        f"agreement={cluster.agreement}, pattern={cluster.pattern}, "
        f"average_delta={cluster.average_delta:.3f}."
    )
    return BenchmarkMiningSuggestion(
        priority=priority,
        benchmark_focus=focus,
        failure_type=cluster.failure_type,
        pattern=cluster.pattern,
        suggested_case_shape=case_shape,
        rationale=rationale,
        evidence_count=cluster.count,
        examples=cluster.examples,
    )


def _priority(cluster: PatchJudgeFailureCluster) -> str:
    if cluster.count >= 3 or abs(cluster.average_delta) >= 0.35:
        return "high"
    if cluster.count >= 2 or abs(cluster.average_delta) >= 0.20:
        return "medium"
    return "low"


def _benchmark_focus(cluster: PatchJudgeFailureCluster) -> str:
    if cluster.agreement == "judge_more_optimistic":
        return "judge false-positive hardening"
    if cluster.agreement == "judge_more_conservative":
        return "judge false-negative recovery"
    if cluster.failure_type == "test_failure":
        return "near-miss semantic repair"
    return "execution-evidence calibration"


def _suggested_case_shape(cluster: PatchJudgeFailureCluster) -> str:
    if cluster.failure_type in {"syntax_error", "import_error", "patch_apply_error"}:
        return (
            "Add cases with attractive but non-executable decoy patches and require "
            "sandbox evidence to cap judge confidence."
        )
    if cluster.failure_type == "timeout":
        return (
            "Add cases with loop-bound or recursion decoys that pass static checks "
            "but timeout in sandbox."
        )
    if cluster.failure_type == "test_failure":
        return (
            "Add near-miss semantic repair cases where partial tests pass but "
            "assertions still expose contract drift."
        )
    if cluster.failure_type in {"type_error", "attribute_error", "runtime_error"}:
        return (
            "Add runtime-exception repair cases with plausible low-risk diffs that "
            "still fail traceback validation."
        )
    return (
        "Add benchmark cases matching this judge/evidence disagreement pattern and "
        "verify calibrated score movement."
    )


def _priority_rank(priority: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(priority, 0)
