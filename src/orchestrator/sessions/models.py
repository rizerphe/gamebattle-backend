"""Game sessions.

A session is one player playing one game revision. The terminal service owns
the event log. These types are the session record and the review responses
this service stores and returns.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

# ``test`` is a player running their own latest snapshot, ``admin`` an admin
# running someone's game, ``comparison`` one side of a comparison.
SessionKind = Literal["test", "admin", "comparison"]


# What the model thinks of a finished comparison session: did the player play
# enough to judge the game? A teacher can also set ``verified_not_enough``.
FairnessVerdict = Literal["enough", "unsure", "not_enough"]
FairnessOverride = Literal["enough", "unsure", "not_enough", "verified_not_enough"]


@dataclass(frozen=True)
class Fairness:
    """The model's answer for a session. ``verdict`` is unset when it timed out or failed."""

    status: Literal["done", "timeout", "error"]
    verdict: FairnessVerdict | None
    reason: str | None
    duration: float | None
    model: str
    evaluated_at: float


@dataclass(frozen=True)
class Session:
    id: UUID
    game: UUID
    game_revision: UUID
    player: UUID
    started_at: float
    recording_seconds: float = 0.0
    kind: SessionKind = "test"
    closed_at: float | None = None
    fairness: Fairness | None = None
    fairness_override: FairnessOverride | None = None


@dataclass(frozen=True)
class PlayerSession:
    """A session as its player sees it.

    A comparison session names someone else's game, and a game's id is its
    author's id, so neither ``game``, ``game_revision`` nor ``player`` is shown.
    """

    id: UUID
    started_at: float
    recording_seconds: float = 0.0


def for_viewer(session: Session, *, admin: bool) -> Session | PlayerSession:
    if admin:
        return session
    return PlayerSession(session.id, session.started_at, session.recording_seconds)


@dataclass(frozen=True)
class Ticket:
    ticket: str
    expires_at: float


@dataclass(frozen=True)
class TerminalState:
    """The serialized terminal after event ``sequence``; a recording chunk starts with one."""

    sequence: int
    elapsed: float
    type: str
    columns: int
    rows: int
    state: str


@dataclass(frozen=True)
class RecordedEvent:
    """An event in a recording chunk. Output and input carry base64 ``data``; a resize carries its size."""

    sequence: int
    elapsed: float
    type: str
    data: str | None = None
    columns: int | None = None
    rows: int | None = None


@dataclass(frozen=True)
class RecordingChunk:
    """A checkpoint and every event after it, for the viewer to replay. ``next`` is where the following chunk starts."""

    checkpoint: TerminalState
    events: list[RecordedEvent]
    next: float | None


@dataclass(frozen=True)
class TerminalEvent:
    """One recorded terminal event, for the review timeline."""

    sequence: int
    elapsed: float
    type: str
