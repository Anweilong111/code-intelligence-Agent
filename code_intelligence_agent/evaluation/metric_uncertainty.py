from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class MetricInterval:
    metric: str
    mean: float
    lower: float
    upper: float
    width: float
    sample_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkMetricUncertaintyReport:
    case_count: int
    confidence_level: float
    bootstrap_samples: int
    seed: int
    metrics: dict[str, MetricInterval]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["metrics"] = {
            name: interval.to_dict()
            for name, interval in self.metrics.items()
        }
        return data


MetricExtractor = tuple[str, Callable[[Any], float]]


DEFAULT_METRICS: tuple[MetricExtractor, ...] = (
    ("top1", lambda case: 1.0 if bool(getattr(case, "top1_hit", False)) else 0.0),
    ("top3", lambda case: 1.0 if bool(getattr(case, "top3_hit", False)) else 0.0),
    ("mrr", lambda case: float(getattr(case, "mrr", 0.0) or 0.0)),
    ("map", lambda case: float(getattr(case, "average_precision", 0.0) or 0.0)),
    ("ndcg_at_3", lambda case: float(getattr(case, "ndcg_at_3", 0.0) or 0.0)),
    ("mean_exam_score", lambda case: float(getattr(case, "exam_score", 1.0) or 0.0)),
    (
        "patch_success_rate",
        lambda case: 1.0 if bool(getattr(case, "patch_success", False)) else 0.0,
    ),
    (
        "multi_patch_success_rate",
        lambda case: 1.0 if bool(getattr(case, "multi_patch_success", False)) else 0.0,
    ),
    (
        "hypothesis_top1",
        lambda case: 1.0 if bool(getattr(case, "hypothesis_top1_hit", False)) else 0.0,
    ),
    (
        "hypothesis_mrr",
        lambda case: float(getattr(case, "hypothesis_mrr", 0.0) or 0.0),
    ),
    (
        "hypothesis_map",
        lambda case: float(
            getattr(case, "hypothesis_average_precision", 0.0) or 0.0
        ),
    ),
    (
        "hypothesis_ndcg_at_3",
        lambda case: float(getattr(case, "hypothesis_ndcg_at_3", 0.0) or 0.0),
    ),
    (
        "hypothesis_mean_exam_score",
        lambda case: float(getattr(case, "hypothesis_exam_score", 1.0) or 0.0),
    ),
)


def metric_uncertainty_summary(report: Any) -> dict[str, Any]:
    uncertainty = benchmark_metric_uncertainty_report(report)
    if uncertainty.case_count == 0:
        return {}
    return uncertainty.to_dict()


def benchmark_metric_uncertainty_report(
    report: Any,
    bootstrap_samples: int = 1000,
    confidence_level: float = 0.95,
    seed: int = 1729,
    metrics: tuple[MetricExtractor, ...] = DEFAULT_METRICS,
) -> BenchmarkMetricUncertaintyReport:
    cases = list(getattr(report, "cases", []))
    if not cases:
        return BenchmarkMetricUncertaintyReport(
            case_count=0,
            confidence_level=confidence_level,
            bootstrap_samples=max(0, bootstrap_samples),
            seed=seed,
            metrics={},
        )

    intervals = {}
    for name, extractor in metrics:
        values = [_clamp(float(extractor(case))) for case in cases]
        intervals[name] = _interval(
            metric=name,
            values=values,
            bootstrap_samples=max(1, bootstrap_samples),
            confidence_level=confidence_level,
            seed=seed,
        )
    return BenchmarkMetricUncertaintyReport(
        case_count=len(cases),
        confidence_level=confidence_level,
        bootstrap_samples=max(1, bootstrap_samples),
        seed=seed,
        metrics=intervals,
    )


def _interval(
    metric: str,
    values: list[float],
    bootstrap_samples: int,
    confidence_level: float,
    seed: int,
) -> MetricInterval:
    mean = _mean(values)
    if len(values) == 1:
        lower = upper = mean
    else:
        rng = random.Random(seed + _stable_metric_offset(metric))
        means = []
        for _ in range(bootstrap_samples):
            sample = [values[rng.randrange(len(values))] for _ in values]
            means.append(_mean(sample))
        alpha = max(0.0, min(1.0, 1.0 - confidence_level))
        lower = _percentile(means, alpha / 2.0)
        upper = _percentile(means, 1.0 - alpha / 2.0)
    return MetricInterval(
        metric=metric,
        mean=round(mean, 4),
        lower=round(lower, 4),
        upper=round(upper, 4),
        width=round(max(0.0, upper - lower), 4),
        sample_count=len(values),
    )


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    position = max(0.0, min(1.0, quantile)) * (len(sorted_values) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    return (
        sorted_values[lower_index] * (1.0 - fraction)
        + sorted_values[upper_index] * fraction
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stable_metric_offset(metric: str) -> int:
    return sum((index + 1) * ord(char) for index, char in enumerate(metric))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
