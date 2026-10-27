"""Reports in MongoDB.

Each report is one document in ``reports`` with its own id and the id of the
session it was filed from, so a session or a player can report more than once.
Queries by game go through ``sessions``. Documents written before reports had
their own id used the session id as ``_id`` and have no ``session`` field.
"""

import logging
import time
from collections.abc import Iterable
from typing import Any
from uuid import UUID, uuid4

from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

from orchestrator.files.mongo import MongoGameFiles
from orchestrator.reports.models import Report, ReportReason
from orchestrator.sessions.models import Session
from orchestrator.sessions.mongo import MongoSessions
from orchestrator.webhooks.notify import Notifier

logger = logging.getLogger("orchestrator")

# A published snapshot stays in rotation until this many different players report it.
REPORTS_BEFORE_UNPUBLISH = 5


class MongoReports:
    def __init__(self, db: AsyncDatabase, notifier: Notifier | None = None) -> None:
        self._reports = db.reports
        self._sessions = MongoSessions(db)
        self._notifier = notifier

    async def file(
        self, session: Session, short_reason: ReportReason, reason: str
    ) -> Report:
        """Report a session. Every call stores a new report."""

        doc = {
            "_id": uuid4(),
            "session": session.id,
            "short_reason": short_reason.value,
            "reason": reason,
            "timestamp": time.time(),
        }
        await self._reports.insert_one(doc)
        report = _load(doc, session)
        await self._after_filed(report)
        return report

    async def _after_filed(self, report: Report) -> None:
        """Count the report against the current publication, take the game down at the limit, notify."""

        session = report.session
        files = MongoGameFiles(self._reports.database, session.game)
        publication = await files.published_revision()
        if publication is None:
            return
        published, published_at = publication
        if published != session.game_revision:
            return
        reporters = await self._reporters_since(session.game, published, published_at)
        count = len(reporters)
        if count >= REPORTS_BEFORE_UNPUBLISH:
            await files.unpublish()
        if self._notifier is None:
            return
        # Another report from a player already counted does not change the count.
        new_player = session.player not in await self._reporters_since(
            session.game, published, published_at, excluding=report
        )
        try:
            await self._notifier.report_filed(session.game, count, new_player)
            if count >= REPORTS_BEFORE_UNPUBLISH:
                await self._notifier.game_removed(session.game, count)
        except Exception:
            # The report is stored and the game is down; a notification must not undo that.
            logger.exception("could not notify webhooks")

    async def _reporters_since(
        self,
        game: UUID,
        revision: UUID,
        since: float,
        excluding: Report | None = None,
    ) -> set[UUID]:
        return {
            other.session.player
            for other in await self.for_game(game)
            if other.session.game_revision == revision
            and other.timestamp >= since
            and (excluding is None or other.id != excluding.id)
        }

    async def for_session(self, session: Session) -> list[Report]:
        docs = await self._find([session.id])
        return [_load(doc, session) for doc in docs]

    async def for_game(self, game: UUID) -> list[Report]:
        sessions = {session.id: session for session in await self._sessions.for_game(game)}
        return await self._reports_of(sessions)

    async def filed_by(self, player: UUID) -> list[Report]:
        sessions = {s.id: s for s in await self._sessions.for_player(player)}
        return await self._reports_of(sessions)

    async def all(self) -> list[Report]:
        docs = await self._reports.find().sort("timestamp", ASCENDING).to_list()
        sessions = await self._sessions.get_many(_session_id(doc) for doc in docs)
        return [
            _load(doc, sessions[_session_id(doc)])
            for doc in docs
            if _session_id(doc) in sessions
        ]

    async def _reports_of(self, sessions: dict[UUID, Session]) -> list[Report]:
        docs = await self._find(list(sessions))
        return [_load(doc, sessions[_session_id(doc)]) for doc in docs]

    async def _find(self, session_ids: Iterable[UUID]) -> list[dict[str, Any]]:
        ids = list(session_ids)
        cursor = self._reports.find(
            {"$or": [{"session": {"$in": ids}}, {"_id": {"$in": ids}, "session": {"$exists": False}}]}
        )
        return await cursor.sort("timestamp", ASCENDING).to_list()


def _session_id(doc: dict[str, Any]) -> UUID:
    return doc.get("session", doc["_id"])


def _load(doc: dict[str, Any], session: Session) -> Report:
    return Report(
        id=doc["_id"],
        session=session,
        short_reason=ReportReason(doc["short_reason"]),
        reason=doc["reason"],
        timestamp=doc["timestamp"],
    )
