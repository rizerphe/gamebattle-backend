"""Reports against a game, filed from the session that played it.

A report belongs to exactly one session, and a session has at most one report.
The game, its revision, and the reporter are the session's; the report does not
repeat them. Filing a report ends the session, so the end of its event log is
the moment of the report.

Five reports against the snapshot that is published, filed after that snapshot
was published, clear the pointer. Reports of any other snapshot do not, and
publishing again starts the count over.
"""

from dataclasses import dataclass
from enum import StrEnum

from orchestrator.sessions.models import Session


class ReportReason(StrEnum):
    UNCLEAR = "unclear"
    BUGGY = "buggy"
    OTHER = "other"


@dataclass(frozen=True)
class FiledReport:
    """What the reporter gets back. The session names the author's game, so it is left out."""

    short_reason: ReportReason
    reason: str
    timestamp: float


@dataclass(frozen=True)
class Report:
    session: Session
    short_reason: ReportReason
    reason: str
    timestamp: float
