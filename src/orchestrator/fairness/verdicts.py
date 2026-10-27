"""What a session's fairness verdict means for a rating.

A session's effective verdict is the teacher's override, else the model's
verdict, else ``enough``: a timeout, an error, or AI being off never blocks or
discounts anyone. A side of a comparison is judged by its best session, so a
replay that ends worse never lowers it.
"""

from collections.abc import Iterable
from typing import Literal

from orchestrator.sessions.models import Session

Effective = Literal["enough", "unsure", "not_enough", "verified_not_enough"]

_RANK: dict[Effective, int] = {
    "verified_not_enough": 0,
    "not_enough": 1,
    "unsure": 2,
    "enough": 3,
}


def effective(session: Session) -> Effective:
    if session.fairness_override is not None:
        return session.fairness_override
    if session.fairness is not None and session.fairness.verdict is not None:
        return session.fairness.verdict
    return "enough"


def best(sessions: Iterable[Session]) -> Effective | None:
    """The most favourable effective verdict, or None for no sessions."""

    verdicts = [effective(session) for session in sessions]
    return max(verdicts, key=_RANK.__getitem__, default=None)


def side_is_flagged(sessions: Iterable[Session]) -> bool:
    """Whether the player should be warned before rating this side."""

    verdict = best(sessions)
    return verdict is not None and verdict != "enough"


def side_counts(sessions: Iterable[Session]) -> bool:
    """Whether this side lets a rating count: not every session was judged too short."""

    verdict = best(sessions)
    return verdict in ("enough", "unsure")
