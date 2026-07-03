from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PatchJudgeReliabilityRow:
    case: str
    rank: int
    candidate_id: str
    success: bool
    failure_type: str
    verdict: str
    raw_score: float
    calibrated_score: float
    evidence_score: float
    brier: float
    absolute_error: float
    optimism_gap: float
    agreement: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PatchJudgeCalibrationBin:
    lower: float
    upper: float
    count: int
    average_score: float
    success_rate: float
    gap: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PatchJudgeReliabilityReport:
    judged_candidate_count: int
    successful_candidate_count: int
    agreement_rate: float
    brier_score: float
    expected_calibration_error: float
    maximum_calibration_error: float
    mean_absolute_error: float
    average_raw_score: float
    average_calibrated_score: float
    average_evidence_score: float
    average_optimism_gap: float
    agreement_counts: dict[str, int]
    verdict_counts: dict[str, int]
    failure_type_counts: dict[str, int]
    bins: list[PatchJudgeCalibrationBin]
    rows: list[PatchJudgeReliabilityRow]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bins"] = [item.to_dict() for item in self.bins]
        data["rows"] = [item.to_dict() for item in self.rows]
        return data


def patch_judge_reliability_summary(report: Any) -> dict[str, Any]:
    reliability = patch_judge_reliability_report(report)
    if reliability.judged_candidate_count == 0:
        return {}
    return reliability.to_dict()


def patch_judge_reliability_report(
    report: Any,
    bin_count: int = 10,
) -> PatchJudgeReliabilityReport:
    rows = patch_judge_reliability_rows(report)
    if not rows:
        return _empty_report()

    total = len(rows)
    success_count = sum(1 for row in rows if row.success)
    agreement_counts = _count_by(rows, "agreement")
    bins = _calibration_bins(rows, bin_count=max(1, bin_count))
    ece = sum(item.count / total * item.gap for item in bins)
    mce = max((item.gap for item in bins), default=0.0)
    return PatchJudgeReliabilityReport(
        judged_candidate_count=total,
        successful_candidate_count=success_count,
        agreement_rate=round(agreement_counts.get("aligned", 0) / total, 4),
        brier_score=round(sum(row.brier for row in rows) / total, 4),
        expected_calibration_error=round(ece, 4),
        maximum_calibration_error=round(mce, 4),
        mean_absolute_error=round(sum(row.absolute_error for row in rows) / total, 4),
        average_raw_score=round(sum(row.raw_score for row in rows) / total, 4),
        average_calibrated_score=round(
            sum(row.calibrated_score for row in rows) / total,
            4,
        ),
        average_evidence_score=round(
            sum(row.evidence_score for row in rows) / total,
            4,
        ),
        average_optimism_gap=round(sum(row.optimism_gap for row in rows) / total, 4),
        agreement_counts=agreement_counts,
        verdict_counts=_count_by(rows, "verdict"),
        failure_type_counts=_count_by(rows, "failure_type"),
        bins=bins,
        rows=rows,
    )


def patch_judge_reliability_rows(report: Any) -> list[PatchJudgeReliabilityRow]:
    rows: list[PatchJudgeReliabilityRow] = []
    for case in getattr(report, "cases", []):
        case_name = str(getattr(case, "case_name", ""))
        for result in getattr(case, "beam_search_results", []):
            if not isinstance(result, dict):
                continue
            judgment = result.get("patch_judgment", {})
            if not isinstance(judgment, dict) or "score" not in judgment:
                continue
            raw_score = _clamp(float(judgment.get("score", 0.0) or 0.0))
            calibrated_score = _clamp(
                float(judgment.get("calibrated_score", raw_score) or 0.0)
            )
            success = bool(result.get("success", False))
            evidence_score = _execution_evidence_score(result)
            label = 1.0 if success else 0.0
            brier = (calibrated_score - label) ** 2
            absolute_error = abs(calibrated_score - evidence_score)
            optimism_gap = calibrated_score - evidence_score
            rows.append(
                PatchJudgeReliabilityRow(
                    case=case_name,
                    rank=int(result.get("rank", 0) or 0),
                    candidate_id=str(result.get("candidate_id", "")),
                    success=success,
                    failure_type=str(result.get("failure_type", "")) or "unknown",
                    verdict=str(judgment.get("verdict", "")),
                    raw_score=round(raw_score, 4),
                    calibrated_score=round(calibrated_score, 4),
                    evidence_score=round(evidence_score, 4),
                    brier=round(brier, 4),
                    absolute_error=round(absolute_error, 4),
                    optimism_gap=round(optimism_gap, 4),
                    agreement=_agreement(optimism_gap),
                )
            )
    return rows


def _execution_evidence_score(result: dict[str, Any]) -> float:
    if bool(result.get("success", False)):
        return 1.0
    passed = float(result.get("passed", 0) or 0)
    failed = float(result.get("failed", 0) or 0)
    total = passed + failed
    passed_ratio = passed / total if total > 0 else 0.0
    feedback_score = _clamp(float(result.get("feedback_score", 0.0) or 0.0))
    risk_payload = result.get("risk", {})
    if isinstance(risk_payload, dict) and "score" in risk_payload:
        risk_score = _clamp(float(risk_payload.get("score", 0.0) or 0.0))
    else:
        risk_score = _clamp(float(result.get("risk_score", 0.0) or 0.0))
    score = 0.45 * passed_ratio + 0.35 * feedback_score + 0.20 * (1.0 - risk_score)
    return _clamp(score)


def _calibration_bins(
    rows: list[PatchJudgeReliabilityRow],
    bin_count: int,
) -> list[PatchJudgeCalibrationBin]:
    buckets: list[list[PatchJudgeReliabilityRow]] = [[] for _ in range(bin_count)]
    for row in rows:
        index = min(bin_count - 1, int(row.calibrated_score * bin_count))
        buckets[index].append(row)

    output = []
    width = 1.0 / bin_count
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        average_score = sum(row.calibrated_score for row in bucket) / len(bucket)
        success_rate = sum(1.0 for row in bucket if row.success) / len(bucket)
        output.append(
            PatchJudgeCalibrationBin(
                lower=round(index * width, 4),
                upper=round((index + 1) * width, 4),
                count=len(bucket),
                average_score=round(average_score, 4),
                success_rate=round(success_rate, 4),
                gap=round(abs(average_score - success_rate), 4),
            )
        )
    return output


def _agreement(optimism_gap: float, tolerance: float = 0.25) -> str:
    if optimism_gap > tolerance:
        return "judge_more_optimistic"
    if optimism_gap < -tolerance:
        return "judge_more_conservative"
    return "aligned"


def _count_by(rows: list[PatchJudgeReliabilityRow], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(getattr(row, field))
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _empty_report() -> PatchJudgeReliabilityReport:
    return PatchJudgeReliabilityReport(
        judged_candidate_count=0,
        successful_candidate_count=0,
        agreement_rate=0.0,
        brier_score=0.0,
        expected_calibration_error=0.0,
        maximum_calibration_error=0.0,
        mean_absolute_error=0.0,
        average_raw_score=0.0,
        average_calibrated_score=0.0,
        average_evidence_score=0.0,
        average_optimism_gap=0.0,
        agreement_counts={},
        verdict_counts={},
        failure_type_counts={},
        bins=[],
        rows=[],
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
