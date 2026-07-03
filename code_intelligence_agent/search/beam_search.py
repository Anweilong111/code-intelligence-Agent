from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class BeamNode(Generic[T]):
    state: T
    score: float
    depth: int = 0
    trace: list[str] = field(default_factory=list)


class BeamSearch(Generic[T]):
    def __init__(self, beam_width: int = 3, max_depth: int = 3) -> None:
        self.beam_width = beam_width
        self.max_depth = max_depth

    def search(
        self,
        initial: list[BeamNode[T]],
        expand: Callable[[BeamNode[T]], list[BeamNode[T]]],
        is_goal: Callable[[BeamNode[T]], bool] | None = None,
    ) -> list[BeamNode[T]]:
        beam = sorted(initial, key=lambda node: node.score, reverse=True)[
            : self.beam_width
        ]
        best = list(beam)
        for _ in range(self.max_depth):
            goals = [node for node in beam if is_goal and is_goal(node)]
            if goals:
                return sorted(goals, key=lambda node: node.score, reverse=True)
            expanded: list[BeamNode[T]] = []
            for node in beam:
                expanded.extend(expand(node))
            if not expanded:
                break
            beam = sorted(expanded, key=lambda node: node.score, reverse=True)[
                : self.beam_width
            ]
            best.extend(beam)
        return sorted(best, key=lambda node: node.score, reverse=True)[
            : self.beam_width
        ]

