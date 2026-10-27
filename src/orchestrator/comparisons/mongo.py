"""Comparisons and Elo in MongoDB.

A comparison document keeps the two session lists, the rating once it is
accepted, and the sessions already observed to have exited on their own. Elo
for a game is one document in ``ratings``, created by its first counted vote.
"""

from typing import Any
from uuid import UUID

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from orchestrator.comparisons.models import Comparison, RankedGame
from orchestrator.comparisons.pairing import INITIAL_ELO, updated_elos
from orchestrator.sessions.mongo import MongoSessions


async def create_indexes(db: AsyncDatabase) -> None:
    await db.comparisons.create_index([("player", ASCENDING), ("rating", ASCENDING)])
    await db.comparisons.create_index([("started_at", DESCENDING)])


class MongoComparisons:
    def __init__(self, db: AsyncDatabase):
        self._comparisons = db.comparisons
        self._sessions = MongoSessions(db)

    async def add(self, comparison: Comparison) -> None:
        await self._comparisons.insert_one(_dump(comparison))

    async def get(self, comparison_id: UUID) -> Comparison | None:
        found = await self._comparisons.find_one({"_id": comparison_id})
        return _load(found) if found else None

    async def list_all(self) -> list[Comparison]:
        docs = await self._comparisons.find({}).sort("started_at", DESCENDING).to_list()
        return [_load(document) for document in docs]

    async def open_for(self, player: UUID) -> Comparison | None:
        found = await self._comparisons.find_one(
            {"player": player, "rating": None, "abandoned": {"$ne": True}}
        )
        return _load(found) if found else None

    async def abandon_open(self) -> None:
        await self._comparisons.update_many(
            {"rating": None, "abandoned": {"$ne": True}}, {"$set": {"abandoned": True}}
        )

    async def rated_by(self, player: UUID) -> int:
        """How many comparisons this player has rated."""

        return await self._comparisons.count_documents(
            {"player": player, "rating": {"$ne": None}}
        )

    async def assigned_games(self, player: UUID) -> set[UUID]:
        """Games this player has already been given, in any comparison."""

        docs = await self._comparisons.find({"player": player}, {"sessions": 1}).to_list()
        session_ids = [session_id for doc in docs for side in doc["sessions"] for session_id in side]
        if not session_ids:
            return set()
        sessions = await self._sessions.get_many(session_ids)
        return {session.game for session in sessions.values()}

    async def append(self, comparison_id: UUID, side: int, session_id: UUID) -> Comparison:
        await self._comparisons.update_one(
            {"_id": comparison_id}, {"$push": {f"sessions.{side}": session_id}}
        )
        found = await self.get(comparison_id)
        if found is None:
            raise KeyError(comparison_id)
        return found

    async def remember_exits(self, comparison_id: UUID, session_ids: list[UUID]) -> Comparison:
        if session_ids:
            await self._comparisons.update_one(
                {"_id": comparison_id}, {"$addToSet": {"exited": {"$each": session_ids}}}
            )
        found = await self.get(comparison_id)
        if found is None:
            raise KeyError(comparison_id)
        return found

    async def record_rating(self, comparison_id: UUID, score: float) -> bool:
        """Store the rating if it is still unset. False when another request won."""

        result = await self._comparisons.update_one(
            {"_id": comparison_id, "rating": None}, {"$set": {"rating": score}}
        )
        return result.modified_count == 1


class MongoRatings:
    def __init__(self, db: AsyncDatabase):
        self._ratings = db.ratings
        self._players = db.players

    async def get(self, game_id: UUID) -> tuple[float, int]:
        found = await self._ratings.find_one({"_id": game_id})
        if found is None:
            return INITIAL_ELO, 0
        return float(found["elo"]), int(found["plays"])

    async def all_plays(self) -> dict[UUID, tuple[float, int]]:
        rows = await self._ratings.find({}).to_list()
        return {row["_id"]: (float(row["elo"]), int(row["plays"])) for row in rows}

    async def apply(self, left: UUID, right: UUID, score: float) -> None:
        elo_left, plays_left = await self.get(left)
        elo_right, plays_right = await self.get(right)
        new_left, new_right = updated_elos(elo_left, elo_right, score)
        await self._ratings.update_one(
            {"_id": left},
            {"$set": {"elo": new_left, "plays": plays_left + 1}},
            upsert=True,
        )
        await self._ratings.update_one(
            {"_id": right},
            {"$set": {"elo": new_right, "plays": plays_right + 1}},
            upsert=True,
        )

    async def ranking(self) -> list[RankedGame]:
        rows = await self._ratings.find({"plays": {"$gt": 0}}).to_list()
        if not rows:
            return []
        players = await self._players.find({"_id": {"$in": [row["_id"] for row in rows]}}).to_list()
        names = {
            document["_id"]: document.get("name")
            if isinstance(document.get("name"), str)
            else None
            for document in players
        }
        entries = [
            RankedGame(
                game_id=row["_id"],
                name=names.get(row["_id"]),
                score=float(row["elo"]),
                plays=int(row["plays"]),
            )
            for row in rows
        ]
        return sorted(entries, key=lambda entry: (-entry.score, entry.name or ""))


def _dump(comparison: Comparison) -> dict[str, Any]:
    return {
        "_id": comparison.id,
        "player": comparison.player,
        "sessions": [list(side) for side in comparison.sessions],
        "rating": comparison.rating,
        "exited": list(comparison.exited),
        "started_at": comparison.started_at,
        "abandoned": comparison.abandoned,
    }


def _load(document: dict[str, Any]) -> Comparison:
    sides = document["sessions"]
    return Comparison(
        id=document["_id"],
        player=document["player"],
        sessions=(tuple(sides[0]), tuple(sides[1])),
        rating=document.get("rating"),
        exited=frozenset(document.get("exited", ())),
        started_at=float(document.get("started_at") or 0),
        abandoned=bool(document.get("abandoned", False)),
    )
