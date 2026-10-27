"""A player comparing two games.

Each side is an ordered list of sessions. The first session is the original
launch. A restart appends another session of the same game; a report appends a
replacement game. ``rating`` is the result for the first list, from 0 to 1, and
stays unset until both current games have genuinely exited at least once.
"""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

ComparisonPhase = Literal["running", "awaiting_rating", "rated", "abandoned"]


@dataclass(frozen=True)
class Comparison:
    id: UUID
    player: UUID
    sessions: tuple[tuple[UUID, ...], tuple[UUID, ...]]
    rating: float | None = None
    exited: frozenset[UUID] = field(default_factory=frozenset)
    started_at: float = 0.0
    abandoned: bool = False
    # When the rating was accepted, and whether the player rated anyway after a
    # fairness warning. Both are set with ``rating``.
    rated_at: float | None = None
    confirmed_warning: bool = False


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
class RankedGame:
    """Server-side only: the game id never reaches a player."""

    game_id: UUID
    name: str | None
    score: float
    plays: int


@dataclass(frozen=True)
class LeaderboardEntry:
    """What the leaderboard shows. ``own`` marks the viewer's game; there is no id."""

    name: str | None
    score: float
    times_played: int
    own: bool


@dataclass(frozen=True)
class ComparisonSideSession:
    session_id: UUID
    game_id: UUID
    game_name: str | None
    recording_seconds: float = 0.0


@dataclass(frozen=True)
class ComparisonActivity:
    """Admin activity feed: a comparison plus display fields."""

    id: UUID
    player: UUID
    sessions: tuple[tuple[UUID, ...], tuple[UUID, ...]]
    rating: float | None
    started_at: float
    voter_email: str | None
    game_ids: tuple[UUID | None, UUID | None]
    game_names: tuple[str | None, str | None]
    phase: ComparisonPhase
    sides_ready: tuple[bool, bool]
    side_sessions: tuple[
        tuple[ComparisonSideSession, ...], tuple[ComparisonSideSession, ...]
    ]
