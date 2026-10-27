"""A player comparing two games.

Each side is an ordered list of sessions. The first session is the original
launch. A restart appends another session of the same game; a report appends a
replacement game. ``rating`` is the result for the first list, from 0 to 1, and
stays unset until both current games have genuinely exited at least once.
"""

from dataclasses import dataclass, field
from uuid import UUID




@dataclass(frozen=True)
class Comparison:
    id: UUID
    player: UUID
    sessions: tuple[tuple[UUID, ...], tuple[UUID, ...]]
    rating: float | None = None
    exited: frozenset[UUID] = field(default_factory=frozenset)
    started_at: float = 0.0
    abandoned: bool = False


@dataclass(frozen=True)
class ComparisonView:
    """What the HTTP API returns."""

    id: UUID
    player: UUID
    sessions: tuple[tuple[UUID, ...], tuple[UUID, ...]]
    rating: float | None = None
    sides_ready: tuple[bool, bool] = (False, False)
    game_names: tuple[str | None, str | None] = (None, None)


@dataclass(frozen=True)
class LeaderboardEntry:
    game_id: UUID
    name: str | None
    score: float