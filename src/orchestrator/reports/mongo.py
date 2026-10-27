"""Reports in MongoDB.

Each report is one document in ``reports`` whose ``_id`` is the session id, so
a session cannot be reported twice. Queries by game go through ``sessions``.
"""

import time
from typing import Any
from uuid import UUID

from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from orchestrator.files.mongo import MongoGameFiles
from orchestrator.reports.models import Report, ReportReason
from orchestrator.sessions.models import Session
from orchestrator.sessions.mongo import MongoSessions

# A published snapshot stays in rotation until this many reports land on it.
REPORTS_BEFORE_UNPUBLISH = 5


class MongoReports:
    def __init__(self, db: AsyncDatabase) -> None:
        self._reports = db.reports
        self._sessions = MongoSessions(db)

    async def file(
        self, session: Session, short_reason: ReportReason, reason: str
    ) -> Report:
        """Report a session. A session that is already reported keeps its first report."""

        doc = {
            "_id": session.id,
            "short_reason": short_reason.value,
            "reason": reason,
            "timestamp": time.time(),
        }
        try:
            await self._reports.insert_one(doc)
        except DuplicateKeyError:
            doc = await self._reports.find_one({"_id": session.id})
            return _load(doc, session)
        await self._unpublish_if_accumulated(session)
        return _load(doc, session)

    async def _unpublish_if_accumulated(self, session: Session) -> None:
        """Take down a game once its current publication has enough reports."""

        publication = await MongoGameFiles(
            self._reports.database, session.game
        ).published_revision()
        if publication is None:
            return
        published, published_at = publication
        if published != session.game_revision:
            return
        count = await self._count_since(session.game, published, published_at)
        if count < REPORTS_BEFORE_UNPUBLISH:
            return
        await MongoGameFiles(self._reports.database, session.game).unpublish()

    async def _count_since(self, game: UUID, revision: UUID, since: float) -> int:
        reports = await self.for_game(game)
        return sum(
            1
            for report in reports
            if report.session.game_revision == revision and report.timestamp >= since
        )

    async def get(self, session: Session) -> Report | None:
        doc = await self._reports.find_one({"_id": session.id})
        return _load(doc, session) if doc else None

    async def for_game(self, game: UUID) -> list[Report]:
        sessions = {session.id: session for session in await self._sessions.for_game(game)}
        cursor = self._reports.find({"_id": {"$in": list(sessions)}})
        docs = await cursor.sort("timestamp", ASCENDING).to_list()
        return [_load(doc, sessions[doc["_id"]]) for doc in docs]

def _load(doc: dict[str, Any], session: Session) -> Report:
    return Report(
        session=session,
        short_reason=ReportReason(doc["short_reason"]),
        reason=doc["reason"],
        timestamp=doc["timestamp"],
    )
