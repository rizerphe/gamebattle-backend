"""Reports in MongoDB.

Each report is one document in ``reports`` whose ``_id`` is the session id, so
a session cannot be reported twice. Queries by game go through ``sessions``.
"""

import logging
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
from orchestrator.webhooks.notify import Notifier

logger = logging.getLogger("orchestrator")

# A published snapshot stays in rotation until this many reports land on it.
REPORTS_BEFORE_UNPUBLISH = 5


class MongoReports:
    def __init__(self, db: AsyncDatabase, notifier: Notifier | None = None) -> None:
        self._reports = db.reports
        self._sessions = MongoSessions(db)
        self._notifier = notifier

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
        await self._after_filed(session)
        return _load(doc, session)

    async def _after_filed(self, session: Session) -> None:
        """Count the report against the current publication, notify, and take the game down at the limit."""

        files = MongoGameFiles(self._reports.database, session.game)
        publication = await files.published_revision()
        if publication is None:
            return
        published, published_at = publication
        if published != session.game_revision:
            return
        count = await self._count_since(session.game, published, published_at)
        if count >= REPORTS_BEFORE_UNPUBLISH:
            await files.unpublish()
        if self._notifier is None:
            return
        try:
            await self._notifier.report_filed(session.game, count)
            if count >= REPORTS_BEFORE_UNPUBLISH:
                await self._notifier.game_removed(session.game, count)
        except Exception:
            # The report is stored and the game is down; a notification must not undo that.
            logger.exception("could not notify webhooks")

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

    async def filed_by(self, player: UUID) -> list[Report]:
        sessions = {s.id: s for s in await self._sessions.for_player(player)}
        cursor = self._reports.find({"_id": {"$in": list(sessions)}})
        docs = await cursor.sort("timestamp", ASCENDING).to_list()
        return [_load(doc, sessions[doc["_id"]]) for doc in docs]

    async def all(self) -> list[Report]:
        docs = await self._reports.find().sort("timestamp", ASCENDING).to_list()
        sessions = await self._sessions.get_many(doc["_id"] for doc in docs)
        return [_load(doc, sessions[doc["_id"]]) for doc in docs if doc["_id"] in sessions]


def _load(doc: dict[str, Any], session: Session) -> Report:
    return Report(
        session=session,
        short_reason=ReportReason(doc["short_reason"]),
        reason=doc["reason"],
        timestamp=doc["timestamp"],
    )
