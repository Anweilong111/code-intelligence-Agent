from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CandidateDiversity:
    source_fingerprint: str
    edit_fingerprint: str
    source_novelty: float
    edit_novelty: float
    novelty_score: float
    accepted: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def candidate_diversity(
    *,
    old_source: str,
    new_source: str,
    failed_sources: list[str] | None = None,
    accepted_sources: list[str] | None = None,
    min_novelty: float = 0.08,
) -> CandidateDiversity:
    failed_sources = failed_sources or []
    accepted_sources = accepted_sources or []
    source_fingerprint = stable_source_fingerprint(new_source)
    edit_fingerprint = stable_source_fingerprint(
        "\n".join(_changed_lines(old_source, new_source))
    )
    if source_fingerprint in {
        stable_source_fingerprint(source) for source in failed_sources
    }:
        return CandidateDiversity(
            source_fingerprint=source_fingerprint,
            edit_fingerprint=edit_fingerprint,
            source_novelty=0.0,
            edit_novelty=0.0,
            novelty_score=0.0,
            accepted=False,
            reason="matches_failed_source_fingerprint",
        )

    source_novelty = _min_novelty(
        _tokens(new_source),
        [_tokens(source) for source in accepted_sources],
    )
    edit_novelty = _min_novelty(
        _tokens("\n".join(_changed_lines(old_source, new_source))),
        [
            _tokens("\n".join(_changed_lines(old_source, source)))
            for source in accepted_sources
        ],
    )
    novelty_score = round(0.45 * source_novelty + 0.55 * edit_novelty, 4)
    accepted = not accepted_sources or novelty_score >= min_novelty
    return CandidateDiversity(
        source_fingerprint=source_fingerprint,
        edit_fingerprint=edit_fingerprint,
        source_novelty=round(source_novelty, 4),
        edit_novelty=round(edit_novelty, 4),
        novelty_score=novelty_score,
        accepted=accepted,
        reason="accepted" if accepted else "low_novelty_against_batch",
    )


def stable_source_fingerprint(source: str) -> str:
    normalized = "\n".join(line.rstrip() for line in source.strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _changed_lines(old_source: str, new_source: str) -> list[str]:
    old_lines = [line.rstrip() for line in old_source.strip().splitlines()]
    new_lines = [line.rstrip() for line in new_source.strip().splitlines()]
    old_set = set(old_lines)
    return [line for line in new_lines if line and line not in old_set]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|==|!=|<=|>=", text))


def _min_novelty(tokens: set[str], references: list[set[str]]) -> float:
    if not references:
        return 1.0
    if not tokens:
        return 0.0
    max_similarity = max(_jaccard(tokens, reference) for reference in references)
    return max(0.0, 1.0 - max_similarity)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)
