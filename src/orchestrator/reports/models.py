"""Reports against a game, filed from the session that played it.

A report belongs to one session; a session may collect several, and a player
may report the same game again. The game, its revision, and the reporter are the
session's; the report does not repeat them. A report may end its session or let
it keep running, so the report's timestamp is its moment in the recording.

Each report is also assessed by the LLM, when there is one, for whether it
describes a real problem with the game. Reports from five different players
whose reports count, against the snapshot that is published, filed after that
snapshot was published, clear the pointer. Repeat reports by one player count
once. Reports of any other snapshot do not count, and publishing again starts
the count over.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from uuid import UUID

from orchestrator.sessions.models import Session


class ReportReason(StrEnum):
    UNCLEAR = "unclear"
    BUGGY = "buggy"
    OTHER = "other"


@dataclass(frozen=True)
class Credibility:
    """The model's view of a report. ``credible`` is unset while pending or when it failed."""

    status: Literal["pending", "done", "timeout", "error"]
    credible: bool | None = None
    reason: str | None = None
    model: str | None = None
    evaluated_at: float | None = None


@dataclass(frozen=True)
class FiledReport:
    """What the reporter gets back. The session names the author's game, so it is left out."""

    short_reason: ReportReason
    reason: str
    timestamp: float


@dataclass(frozen=True)
class Report:
    id: UUID
    session: Session
    short_reason: ReportReason
    reason: str
    timestamp: float
    credibility: Credibility | None = None


def counts(report: Report) -> bool:
    """Whether the report counts towards taking a game down.

    A report waiting for its assessment does not count yet. One the model never
    judged (no LLM, a timeout, an error) counts, so a broken model cannot
    shield a game.
    """

    credibility = report.credibility
    if credibility is None:
        return True
    if credibility.status == "pending":
        return False
    return credibility.credible is not False
