"""Session records in MongoDB.

Each session is one document in ``sessions``, written when the game
launches. Only ``closed_at`` is set later, when the orchestrator stops it. The event log stays with the terminal service.
"""

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

from orchestrator.sessions.models import Session

_session = TypeAdapter(Session)


async def create_indexes(db: AsyncDatabase) -> None:
    await db.sessions.create_index([("game", ASCENDING), ("started_at", ASCENDING)])
    await db.sessions.create_index([("closed_at", ASCENDING), ("player", ASCENDING)])


class MongoSessions:
    def __init__(self, db: AsyncDatabase) -> None:
        self._sessions = db.sessions

    async def add(self, session: Session) -> None:
        await self._sessions.insert_one(_dump(session))

    async def get(self, session_id: UUID) -> Session | None:
        found = await self._sessions.find_one({"_id": session_id})
        return _load(found) if found else None

    async def get_many(self, session_ids: Iterable[UUID]) -> dict[UUID, Session]:
        cursor = self._sessions.find({"_id": {"$in": list(session_ids)}})
        return {doc["_id"]: _load(doc) for doc in await cursor.to_list()}

    async def for_game(self, game: UUID) -> list[Session]:
        cursor = self._sessions.find({"game": game}).sort("started_at", ASCENDING)
        return [_load(doc) for doc in await cursor.to_list()]

    async def for_player(self, player: UUID) -> list[Session]:
        cursor = self._sessions.find({"player": player}).sort("started_at", ASCENDING)
        return [_load(doc) for doc in await cursor.to_list()]

    async def unclosed_for_player(self, player: UUID) -> list[Session]:
        cursor = self._sessions.find({"player": player, "closed_at": None}).sort(
            "started_at", ASCENDING
        )
        return [_load(doc) for doc in await cursor.to_list()]

    async def unclosed_of_kinds(self, kinds: list[str]) -> list[Session]:
        cursor = self._sessions.find({"kind": {"$in": kinds}, "closed_at": None})
        return [_load(doc) for doc in await cursor.to_list()]

    async def close(self, session_id: UUID, at: float) -> None:
        await self._sessions.update_one(
            {"_id": session_id, "closed_at": None}, {"$set": {"closed_at": at}}
        )


def _dump(session: Session) -> dict[str, Any]:
    doc = _session.dump_python(session, exclude={"recording_seconds", "closed_at"})
    doc["_id"] = doc.pop("id")
    doc["closed_at"] = None
    return doc


def _load(doc: dict[str, Any]) -> Session:
    return _session.validate_python({**doc, "id": doc["_id"]})
