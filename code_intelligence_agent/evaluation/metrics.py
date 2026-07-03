from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalizationRun:
    ranked: list[str]
    ground_truth: set[str]


def top_k_accuracy(runs: list[LocalizationRun], k: int) -> float:
    if not runs:
        return 0.0
    hits = 0
    for run in runs:
        if any(item in run.ground_truth for item in run.ranked[:k]):
            hits += 1
    return hits / len(runs)


def mean_reciprocal_rank(runs: list[LocalizationRun]) -> float:
    if not runs:
        return 0.0
    total = 0.0
    for run in runs:
        rank = _first_relevant_rank(run.ranked, run.ground_truth)
        total += 0.0 if rank is None else 1 / rank
    return total / len(runs)


def average_precision(run: LocalizationRun) -> float:
    if not run.ground_truth:
        return 0.0
    hits = 0
    precision_sum = 0.0
    seen: set[str] = set()
    for index, item in enumerate(run.ranked, start=1):
        if item in seen:
            continue
        seen.add(item)
        if item not in run.ground_truth:
            continue
        hits += 1
        precision_sum += hits / index
    return precision_sum / len(run.ground_truth)


def mean_average_precision(runs: list[LocalizationRun]) -> float:
    if not runs:
        return 0.0
    return sum(average_precision(run) for run in runs) / len(runs)


def normalized_discounted_cumulative_gain(run: LocalizationRun, k: int) -> float:
    if k <= 0 or not run.ground_truth:
        return 0.0
    dcg = 0.0
    seen: set[str] = set()
    for index, item in enumerate(run.ranked[:k], start=1):
        if item in seen:
            continue
        seen.add(item)
        if item in run.ground_truth:
            dcg += 1.0 / math.log2(index + 1)
    ideal_hits = min(len(run.ground_truth), k)
    ideal_dcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    if ideal_dcg == 0.0:
        return 0.0
    return dcg / ideal_dcg


def mean_ndcg(runs: list[LocalizationRun], k: int) -> float:
    if not runs:
        return 0.0
    return sum(normalized_discounted_cumulative_gain(run, k) for run in runs) / len(runs)


def exam_score(run: LocalizationRun) -> float:
    ranked = _unique_ranked(run.ranked)
    if not ranked or not run.ground_truth:
        return 1.0
    for index, item in enumerate(ranked, start=1):
        if item in run.ground_truth:
            return (index - 1) / len(ranked)
    return 1.0


def mean_exam_score(runs: list[LocalizationRun]) -> float:
    if not runs:
        return 0.0
    return sum(exam_score(run) for run in runs) / len(runs)


def patch_success_rate(successes: list[bool]) -> float:
    if not successes:
        return 0.0
    return sum(1 for item in successes if item) / len(successes)


def average(values: list[int | float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _first_relevant_rank(ranked: list[str], ground_truth: set[str]) -> int | None:
    for index, item in enumerate(ranked, start=1):
        if item in ground_truth:
            return index
    return None


def _unique_ranked(ranked: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in ranked:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique
