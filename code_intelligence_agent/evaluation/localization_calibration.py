from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any


CALIBRATION_MODEL = "leave_one_out_beta_binning"
CALIBRATION_PRIOR_STRENGTH = 10.0


@dataclass(frozen=True)
class LocalizationCalibrationRow:
    case: str
    top_function: str
    confidence: float
    top1_hit: bool
    brier: float
    absolute_error: float
    calibration_gap: float
    agreement: str
    calibrated_confidence: float = 0.0
    calibrated_brier: float = 0.0
    calibrated_absolute_error: float = 0.0
    calibrated_gap: float = 0.0
    source_group: str = "unspecified"
    bug_type: str = "unspecified"
    expected_rule_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LocalizationCalibrationBin:
    lower: float
    upper: float
    count: int
    average_confidence: float
    top1_accuracy: float
    gap: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LocalizationCalibrationReport:
    case_count: int
    top1_positive_count: int
    top1_accuracy: float
    brier_score: float
    expected_calibration_error: float
    maximum_calibration_error: float
    mean_absolute_error: float
    average_confidence: float
    overconfidence_rate: float
    underconfidence_rate: float
    agreement_counts: dict[str, int]
    bins: list[LocalizationCalibrationBin]
    rows: list[LocalizationCalibrationRow]
    calibration_model: str
    calibrated_brier_score: float
    calibrated_expected_calibration_error: float
    calibrated_maximum_calibration_error: float
    calibrated_mean_absolute_error: float
    calibrated_average_confidence: float
    brier_score_improvement: float
    expected_calibration_error_improvement: float
    calibrated_bins: list[LocalizationCalibrationBin]
    stratified_groups: list[LocalizationCalibrationGroup]
    source_group_holdout_splits: list[LocalizationCalibrationHoldoutSplit]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bins"] = [item.to_dict() for item in self.bins]
        data["rows"] = [item.to_dict() for item in self.rows]
        data["calibrated_bins"] = [
            item.to_dict() for item in self.calibrated_bins
        ]
        data["stratified_groups"] = [
            item.to_dict() for item in self.stratified_groups
        ]
        data["source_group_holdout_splits"] = [
            item.to_dict() for item in self.source_group_holdout_splits
        ]
        return data


@dataclass(frozen=True)
class LocalizationCalibrationGroup:
    dimension: str
    group: str
    case_count: int
    top1_accuracy: float
    average_confidence: float
    brier_score: float
    expected_calibration_error: float
    calibrated_average_confidence: float
    calibrated_brier_score: float
    calibrated_expected_calibration_error: float
    brier_score_improvement: float
    expected_calibration_error_improvement: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LocalizationCalibrationHoldoutSplit:
    holdout_group: str
    train_case_count: int
    holdout_case_count: int
    holdout_top1_accuracy: float
    holdout_average_confidence: float
    holdout_brier_score: float
    holdout_expected_calibration_error: float
    holdout_calibrated_average_confidence: float
    holdout_calibrated_brier_score: float
    holdout_calibrated_expected_calibration_error: float
    brier_score_improvement: float
    expected_calibration_error_improvement: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def localization_calibration_summary(report: Any) -> dict[str, Any]:
    calibration = localization_calibration_report(report)
    if calibration.case_count == 0:
        return {}
    return calibration.to_dict()


def localization_calibration_report(
    report: Any,
    bin_count: int = 10,
) -> LocalizationCalibrationReport:
    rows = localization_calibration_rows(report)
    if not rows:
        return _empty_report()

    total = len(rows)
    positives = sum(1 for row in rows if row.top1_hit)
    bins = _calibration_bins(rows, bin_count=max(1, bin_count))
    ece = sum(item.count / total * item.gap for item in bins)
    mce = max((item.gap for item in bins), default=0.0)
    agreement_counts = _count_by(rows, "agreement")
    brier_score = round(sum(row.brier for row in rows) / total, 4)
    mean_absolute_error = round(sum(row.absolute_error for row in rows) / total, 4)
    average_confidence = round(sum(row.confidence for row in rows) / total, 4)
    calibrated_rows = _calibrated_rows(
        rows,
        bin_count=max(1, bin_count),
        prior_strength=CALIBRATION_PRIOR_STRENGTH,
    )
    calibrated_bins = _calibration_bins(
        calibrated_rows,
        bin_count=max(1, bin_count),
        confidence_field="calibrated_confidence",
    )
    calibrated_ece = sum(item.count / total * item.gap for item in calibrated_bins)
    calibrated_mce = max((item.gap for item in calibrated_bins), default=0.0)
    calibrated_brier_score = round(
        sum(row.calibrated_brier for row in calibrated_rows) / total,
        4,
    )
    return LocalizationCalibrationReport(
        case_count=total,
        top1_positive_count=positives,
        top1_accuracy=round(positives / total, 4),
        brier_score=brier_score,
        expected_calibration_error=round(ece, 4),
        maximum_calibration_error=round(mce, 4),
        mean_absolute_error=mean_absolute_error,
        average_confidence=average_confidence,
        overconfidence_rate=round(
            agreement_counts.get("overconfident", 0) / total,
            4,
        ),
        underconfidence_rate=round(
            agreement_counts.get("underconfident", 0) / total,
            4,
        ),
        agreement_counts=agreement_counts,
        bins=bins,
        rows=calibrated_rows,
        calibration_model=CALIBRATION_MODEL,
        calibrated_brier_score=calibrated_brier_score,
        calibrated_expected_calibration_error=round(calibrated_ece, 4),
        calibrated_maximum_calibration_error=round(calibrated_mce, 4),
        calibrated_mean_absolute_error=round(
            sum(row.calibrated_absolute_error for row in calibrated_rows) / total,
            4,
        ),
        calibrated_average_confidence=round(
            sum(row.calibrated_confidence for row in calibrated_rows) / total,
            4,
        ),
        brier_score_improvement=round(brier_score - calibrated_brier_score, 4),
        expected_calibration_error_improvement=round(
            round(ece, 4) - round(calibrated_ece, 4),
            4,
        ),
        calibrated_bins=calibrated_bins,
        stratified_groups=_stratified_groups(
            calibrated_rows,
            bin_count=max(1, bin_count),
        ),
        source_group_holdout_splits=_source_group_holdout_splits(
            rows,
            bin_count=max(1, bin_count),
            prior_strength=CALIBRATION_PRIOR_STRENGTH,
        ),
    )


def localization_calibration_rows(report: Any) -> list[LocalizationCalibrationRow]:
    rows = []
    for case in getattr(report, "cases", []):
        details = getattr(case, "localization_details", [])
        if not details:
            continue
        top_detail = details[0]
        if not isinstance(top_detail, dict):
            continue
        confidence = _clamp(float(top_detail.get("score", 0.0) or 0.0))
        top1_hit = bool(getattr(case, "top1_hit", False))
        label = 1.0 if top1_hit else 0.0
        calibration_gap = confidence - label
        rows.append(
            LocalizationCalibrationRow(
                case=str(getattr(case, "case_name", "")),
                top_function=str(top_detail.get("function_name", "")),
                confidence=round(confidence, 4),
                top1_hit=top1_hit,
                brier=round((confidence - label) ** 2, 4),
                absolute_error=round(abs(confidence - label), 4),
                calibration_gap=round(calibration_gap, 4),
                agreement=_agreement(confidence, top1_hit),
                calibrated_confidence=round(confidence, 4),
                calibrated_brier=round((confidence - label) ** 2, 4),
                calibrated_absolute_error=round(abs(confidence - label), 4),
                calibrated_gap=round(calibration_gap, 4),
                source_group=_source_group(case),
                bug_type=_bug_type(case),
                expected_rule_ids=_expected_rule_ids(case),
            )
        )
    return rows


def _stratified_groups(
    rows: list[LocalizationCalibrationRow],
    *,
    bin_count: int,
) -> list[LocalizationCalibrationGroup]:
    groups: dict[tuple[str, str], list[LocalizationCalibrationRow]] = {}
    for row in rows:
        for dimension, group in _row_groups(row):
            groups.setdefault((dimension, group), []).append(row)
    output = [
        _group_summary(
            dimension=dimension,
            group=group,
            rows=items,
            bin_count=bin_count,
        )
        for (dimension, group), items in groups.items()
    ]
    return sorted(
        output,
        key=lambda item: (
            item.dimension,
            -item.case_count,
            item.group,
        ),
    )


def _source_group_holdout_splits(
    rows: list[LocalizationCalibrationRow],
    *,
    bin_count: int,
    prior_strength: float,
) -> list[LocalizationCalibrationHoldoutSplit]:
    groups: dict[str, list[LocalizationCalibrationRow]] = {}
    for row in rows:
        groups.setdefault(row.source_group or "unspecified", []).append(row)
    if len(groups) <= 1:
        return []

    splits = []
    for holdout_group, holdout_rows in sorted(groups.items()):
        train_rows = [
            row
            for group, group_rows in groups.items()
            if group != holdout_group
            for row in group_rows
        ]
        calibrated_holdout_rows = _calibrated_rows_from_training_rows(
            holdout_rows,
            train_rows,
            bin_count=bin_count,
            prior_strength=prior_strength,
        )
        splits.append(
            _holdout_split_summary(
                holdout_group=holdout_group,
                train_case_count=len(train_rows),
                holdout_rows=holdout_rows,
                calibrated_holdout_rows=calibrated_holdout_rows,
                bin_count=bin_count,
            )
        )
    return splits


def _calibrated_rows_from_training_rows(
    holdout_rows: list[LocalizationCalibrationRow],
    train_rows: list[LocalizationCalibrationRow],
    *,
    bin_count: int,
    prior_strength: float,
) -> list[LocalizationCalibrationRow]:
    if not holdout_rows:
        return []
    if not train_rows:
        return list(holdout_rows)

    train_accuracy = sum(1.0 for row in train_rows if row.top1_hit) / len(train_rows)
    prior_mean = max(0.05, min(0.95, train_accuracy))
    counts: dict[int, int] = {}
    positives: dict[int, float] = {}
    for row in train_rows:
        index = _bin_index(row.confidence, bin_count)
        counts[index] = counts.get(index, 0) + 1
        positives[index] = positives.get(index, 0.0) + (1.0 if row.top1_hit else 0.0)

    calibrated_rows = []
    for row in holdout_rows:
        index = _bin_index(row.confidence, bin_count)
        label = 1.0 if row.top1_hit else 0.0
        calibrated_confidence = _clamp(
            (positives.get(index, 0.0) + prior_strength * prior_mean)
            / (counts.get(index, 0) + prior_strength)
        )
        calibrated_gap = calibrated_confidence - label
        calibrated_rows.append(
            replace(
                row,
                calibrated_confidence=round(calibrated_confidence, 4),
                calibrated_brier=round((calibrated_confidence - label) ** 2, 4),
                calibrated_absolute_error=round(
                    abs(calibrated_confidence - label),
                    4,
                ),
                calibrated_gap=round(calibrated_gap, 4),
            )
        )
    return calibrated_rows


def _holdout_split_summary(
    *,
    holdout_group: str,
    train_case_count: int,
    holdout_rows: list[LocalizationCalibrationRow],
    calibrated_holdout_rows: list[LocalizationCalibrationRow],
    bin_count: int,
) -> LocalizationCalibrationHoldoutSplit:
    total = len(holdout_rows)
    top1_accuracy = round(
        sum(1.0 for row in holdout_rows if row.top1_hit) / total,
        4,
    )
    brier_score = round(sum(row.brier for row in holdout_rows) / total, 4)
    calibrated_brier_score = round(
        sum(row.calibrated_brier for row in calibrated_holdout_rows) / total,
        4,
    )
    ece = _ece(holdout_rows, bin_count=bin_count, confidence_field="confidence")
    calibrated_ece = _ece(
        calibrated_holdout_rows,
        bin_count=bin_count,
        confidence_field="calibrated_confidence",
    )
    return LocalizationCalibrationHoldoutSplit(
        holdout_group=holdout_group,
        train_case_count=train_case_count,
        holdout_case_count=total,
        holdout_top1_accuracy=top1_accuracy,
        holdout_average_confidence=round(
            sum(row.confidence for row in holdout_rows) / total,
            4,
        ),
        holdout_brier_score=brier_score,
        holdout_expected_calibration_error=round(ece, 4),
        holdout_calibrated_average_confidence=round(
            sum(row.calibrated_confidence for row in calibrated_holdout_rows)
            / total,
            4,
        ),
        holdout_calibrated_brier_score=calibrated_brier_score,
        holdout_calibrated_expected_calibration_error=round(calibrated_ece, 4),
        brier_score_improvement=round(
            brier_score - calibrated_brier_score,
            4,
        ),
        expected_calibration_error_improvement=round(
            round(ece, 4) - round(calibrated_ece, 4),
            4,
        ),
    )


def _row_groups(row: LocalizationCalibrationRow) -> list[tuple[str, str]]:
    groups = [
        ("source_group", row.source_group or "unspecified"),
        ("bug_type", row.bug_type or "unspecified"),
    ]
    rules = row.expected_rule_ids or ["unspecified"]
    groups.extend(("expected_rule", str(rule)) for rule in rules)
    return groups


def _group_summary(
    *,
    dimension: str,
    group: str,
    rows: list[LocalizationCalibrationRow],
    bin_count: int,
) -> LocalizationCalibrationGroup:
    total = len(rows)
    top1_accuracy = round(sum(1.0 for row in rows if row.top1_hit) / total, 4)
    brier_score = round(sum(row.brier for row in rows) / total, 4)
    calibrated_brier_score = round(
        sum(row.calibrated_brier for row in rows) / total,
        4,
    )
    ece = _ece(rows, bin_count=bin_count, confidence_field="confidence")
    calibrated_ece = _ece(
        rows,
        bin_count=bin_count,
        confidence_field="calibrated_confidence",
    )
    return LocalizationCalibrationGroup(
        dimension=dimension,
        group=group,
        case_count=total,
        top1_accuracy=top1_accuracy,
        average_confidence=round(
            sum(row.confidence for row in rows) / total,
            4,
        ),
        brier_score=brier_score,
        expected_calibration_error=round(ece, 4),
        calibrated_average_confidence=round(
            sum(row.calibrated_confidence for row in rows) / total,
            4,
        ),
        calibrated_brier_score=calibrated_brier_score,
        calibrated_expected_calibration_error=round(calibrated_ece, 4),
        brier_score_improvement=round(
            brier_score - calibrated_brier_score,
            4,
        ),
        expected_calibration_error_improvement=round(
            round(ece, 4) - round(calibrated_ece, 4),
            4,
        ),
    )


def _ece(
    rows: list[LocalizationCalibrationRow],
    *,
    bin_count: int,
    confidence_field: str,
) -> float:
    if not rows:
        return 0.0
    bins = _calibration_bins(
        rows,
        bin_count=max(1, bin_count),
        confidence_field=confidence_field,
    )
    total = len(rows)
    return sum(item.count / total * item.gap for item in bins)


def _source_group(case: Any) -> str:
    metadata = _metadata(case)
    candidates = (
        metadata.get("upstream"),
        metadata.get("source_project"),
        metadata.get("source_repo"),
        metadata.get("repo"),
        metadata.get("project"),
    )
    for candidate in candidates:
        if candidate:
            return str(candidate)
    case_name = str(_get(case, "case_name", ""))
    if case_name.startswith("cpython_"):
        return "python/cpython"
    if case_name.startswith("thealgorithms_"):
        return "TheAlgorithms/Python"
    if case_name.startswith("pluggy_"):
        return "pytest-dev/pluggy"
    return "unspecified"


def _bug_type(case: Any) -> str:
    metadata = _metadata(case)
    return str(
        _get(case, "bug_type", "")
        or metadata.get("bug_type")
        or "unspecified"
    )


def _expected_rule_ids(case: Any) -> list[str]:
    rules = _get(case, "expected_rule_ids", [])
    if not isinstance(rules, list):
        return []
    return [str(rule) for rule in rules if str(rule)]


def _metadata(case: Any) -> dict[str, Any]:
    metadata = _get(case, "metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _calibrated_rows(
    rows: list[LocalizationCalibrationRow],
    *,
    bin_count: int,
    prior_strength: float,
) -> list[LocalizationCalibrationRow]:
    if not rows:
        return []
    global_accuracy = sum(1.0 for row in rows if row.top1_hit) / len(rows)
    prior_mean = max(0.05, min(0.95, global_accuracy))
    counts: dict[int, int] = {}
    positives: dict[int, float] = {}
    row_bins = []
    for row in rows:
        index = _bin_index(row.confidence, bin_count)
        row_bins.append(index)
        counts[index] = counts.get(index, 0) + 1
        positives[index] = positives.get(index, 0.0) + (1.0 if row.top1_hit else 0.0)

    calibrated_rows = []
    for row, index in zip(rows, row_bins):
        label = 1.0 if row.top1_hit else 0.0
        heldout_count = counts[index] - 1
        heldout_positives = positives[index] - label
        calibrated_confidence = _clamp(
            (heldout_positives + prior_strength * prior_mean)
            / (heldout_count + prior_strength)
        )
        calibrated_gap = calibrated_confidence - label
        calibrated_rows.append(
            replace(
                row,
                calibrated_confidence=round(calibrated_confidence, 4),
                calibrated_brier=round((calibrated_confidence - label) ** 2, 4),
                calibrated_absolute_error=round(
                    abs(calibrated_confidence - label),
                    4,
                ),
                calibrated_gap=round(calibrated_gap, 4),
            )
        )
    return calibrated_rows


def _agreement(
    confidence: float,
    top1_hit: bool,
    threshold: float = 0.5,
) -> str:
    if top1_hit and confidence < threshold:
        return "underconfident"
    if not top1_hit and confidence >= threshold:
        return "overconfident"
    return "aligned"


def _calibration_bins(
    rows: list[LocalizationCalibrationRow],
    bin_count: int,
    confidence_field: str = "confidence",
) -> list[LocalizationCalibrationBin]:
    buckets: list[list[LocalizationCalibrationRow]] = [
        [] for _ in range(bin_count)
    ]
    for row in rows:
        confidence = _clamp(float(getattr(row, confidence_field, 0.0) or 0.0))
        index = _bin_index(confidence, bin_count)
        buckets[index].append(row)

    output = []
    width = 1.0 / bin_count
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        average_confidence = (
            sum(
                _clamp(float(getattr(row, confidence_field, 0.0) or 0.0))
                for row in bucket
            )
            / len(bucket)
        )
        top1_accuracy = sum(1.0 for row in bucket if row.top1_hit) / len(bucket)
        output.append(
            LocalizationCalibrationBin(
                lower=round(index * width, 4),
                upper=round((index + 1) * width, 4),
                count=len(bucket),
                average_confidence=round(average_confidence, 4),
                top1_accuracy=round(top1_accuracy, 4),
                gap=round(abs(average_confidence - top1_accuracy), 4),
            )
        )
    return output


def _bin_index(confidence: float, bin_count: int) -> int:
    return min(bin_count - 1, int(_clamp(confidence) * bin_count))


def _count_by(rows: list[LocalizationCalibrationRow], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(getattr(row, field))
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _empty_report() -> LocalizationCalibrationReport:
    return LocalizationCalibrationReport(
        case_count=0,
        top1_positive_count=0,
        top1_accuracy=0.0,
        brier_score=0.0,
        expected_calibration_error=0.0,
        maximum_calibration_error=0.0,
        mean_absolute_error=0.0,
        average_confidence=0.0,
        overconfidence_rate=0.0,
        underconfidence_rate=0.0,
        agreement_counts={},
        bins=[],
        rows=[],
        calibration_model=CALIBRATION_MODEL,
        calibrated_brier_score=0.0,
        calibrated_expected_calibration_error=0.0,
        calibrated_maximum_calibration_error=0.0,
        calibrated_mean_absolute_error=0.0,
        calibrated_average_confidence=0.0,
        brier_score_improvement=0.0,
        expected_calibration_error_improvement=0.0,
        calibrated_bins=[],
        stratified_groups=[],
        source_group_holdout_splits=[],
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
