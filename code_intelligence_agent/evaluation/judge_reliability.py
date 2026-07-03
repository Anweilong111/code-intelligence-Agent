from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CaseJudgeReliabilityRow:
    case: str
    verdict: str
    judge_score: float
    evidence_label: bool
    evidence_score: float
    brier: float
    absolute_error: float
    optimism_gap: float
    agreement: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    average_score: float
    accuracy: float
    gap: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CaseJudgeReliabilityReport:
    judged_case_count: int
    positive_case_count: int
    agreement_rate: float
    brier_score: float
    expected_calibration_error: float
    maximum_calibration_error: float
    mean_absolute_error: float
    average_judge_score: float
    average_evidence_score: float
    average_optimism_gap: float
    agreement_counts: dict[str, int]
    verdict_counts: dict[str, int]
    bins: list[CalibrationBin]
    rows: list[CaseJudgeReliabilityRow]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bins"] = [item.to_dict() for item in self.bins]
        data["rows"] = [item.to_dict() for item in self.rows]
        return data


def case_judge_reliability_summary(report: Any) -> dict[str, Any]:
    reliability = case_judge_reliability_report(report)
    if reliability.judged_case_count == 0:
        return {}
    return reliability.to_dict()


def case_judge_reliability_report(
    report: Any,
    bin_count: int = 10,
) -> CaseJudgeReliabilityReport:
    rows = case_judge_reliability_rows(report)
    if not rows:
        return _empty_report()

    total = len(rows)
    positive_count = sum(1 for row in rows if row.evidence_label)
    agreement_counts = _count_by(rows, "agreement")
    verdict_counts = _count_by(rows, "verdict")
    bins = _calibration_bins(rows, bin_count=max(1, bin_count))
    ece = sum(item.count / total * item.gap for item in bins)
    mce = max((item.gap for item in bins), default=0.0)
    return CaseJudgeReliabilityReport(
        judged_case_count=total,
        positive_case_count=positive_count,
        agreement_rate=round(agreement_counts.get("aligned", 0) / total, 4),
        brier_score=round(sum(row.brier for row in rows) / total, 4),
        expected_calibration_error=round(ece, 4),
        maximum_calibration_error=round(mce, 4),
        mean_absolute_error=round(sum(row.absolute_error for row in rows) / total, 4),
        average_judge_score=round(sum(row.judge_score for row in rows) / total, 4),
        average_evidence_score=round(
            sum(row.evidence_score for row in rows) / total,
            4,
        ),
        average_optimism_gap=round(sum(row.optimism_gap for row in rows) / total, 4),
        agreement_counts=agreement_counts,
        verdict_counts=verdict_counts,
        bins=bins,
        rows=rows,
    )


def case_judge_reliability_rows(report: Any) -> list[CaseJudgeReliabilityRow]:
    rows = []
    for case in getattr(report, "cases", []):
        judgment = getattr(case, "llm_judgment", None)
        if not isinstance(judgment, dict) or "score" not in judgment:
            continue
        judge_score = _clamp(float(judgment.get("score", 0.0) or 0.0))
        evidence_label = _evidence_label(case)
        evidence_score = _evidence_score(case)
        label_value = 1.0 if evidence_label else 0.0
        brier = (judge_score - label_value) ** 2
        absolute_error = abs(judge_score - evidence_score)
        optimism_gap = judge_score - evidence_score
        rows.append(
            CaseJudgeReliabilityRow(
                case=str(getattr(case, "case_name", "")),
                verdict=str(judgment.get("verdict", "")),
                judge_score=round(judge_score, 4),
                evidence_label=evidence_label,
                evidence_score=round(evidence_score, 4),
                brier=round(brier, 4),
                absolute_error=round(absolute_error, 4),
                optimism_gap=round(optimism_gap, 4),
                agreement=_agreement(optimism_gap),
            )
        )
    return rows


def _evidence_label(case: Any) -> bool:
    return (
        bool(getattr(case, "top1_hit", False))
        and bool(getattr(case, "patch_success", False))
        and _rule_recall(case) >= 1.0
    )


def _evidence_score(case: Any) -> float:
    if bool(getattr(case, "top1_hit", False)):
        localization = 1.0
    elif bool(getattr(case, "top3_hit", False)):
        localization = 0.7
    elif float(getattr(case, "mrr", 0.0) or 0.0) > 0.0:
        localization = min(0.6, float(getattr(case, "mrr", 0.0) or 0.0))
    else:
        localization = 0.0

    repair = 1.0 if bool(getattr(case, "patch_success", False)) else 0.0
    risk = _risk_evidence(case)
    score = 0.35 * localization + 0.20 * _rule_recall(case)
    score += 0.35 * repair + 0.10 * risk
    return _clamp(score)


def _rule_recall(case: Any) -> float:
    expected_rules = getattr(case, "expected_rule_ids", [])
    if not expected_rules:
        return 1.0
    return _clamp(float(getattr(case, "expected_rule_recall", 0.0) or 0.0))


def _risk_evidence(case: Any) -> float:
    risk = getattr(case, "best_patch_risk", None)
    if not isinstance(risk, dict) or "score" not in risk:
        return 1.0 if bool(getattr(case, "patch_success", False)) else 0.0
    return 1.0 - _clamp(float(risk.get("score", 0.0) or 0.0))


def _agreement(optimism_gap: float, tolerance: float = 0.25) -> str:
    if optimism_gap > tolerance:
        return "judge_more_optimistic"
    if optimism_gap < -tolerance:
        return "judge_more_conservative"
    return "aligned"


def _calibration_bins(
    rows: list[CaseJudgeReliabilityRow],
    bin_count: int,
) -> list[CalibrationBin]:
    buckets: list[list[CaseJudgeReliabilityRow]] = [[] for _ in range(bin_count)]
    for row in rows:
        index = min(bin_count - 1, int(row.judge_score * bin_count))
        buckets[index].append(row)

    output = []
    width = 1.0 / bin_count
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        average_score = sum(row.judge_score for row in bucket) / len(bucket)
        accuracy = (
            sum(1.0 for row in bucket if row.evidence_label) / len(bucket)
        )
        output.append(
            CalibrationBin(
                lower=round(index * width, 4),
                upper=round((index + 1) * width, 4),
                count=len(bucket),
                average_score=round(average_score, 4),
                accuracy=round(accuracy, 4),
                gap=round(abs(average_score - accuracy), 4),
            )
        )
    return output


def _count_by(rows: list[CaseJudgeReliabilityRow], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(getattr(row, field))
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _empty_report() -> CaseJudgeReliabilityReport:
    return CaseJudgeReliabilityReport(
        judged_case_count=0,
        positive_case_count=0,
        agreement_rate=0.0,
        brier_score=0.0,
        expected_calibration_error=0.0,
        maximum_calibration_error=0.0,
        mean_absolute_error=0.0,
        average_judge_score=0.0,
        average_evidence_score=0.0,
        average_optimism_gap=0.0,
        agreement_counts={},
        verdict_counts={},
        bins=[],
        rows=[],
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
