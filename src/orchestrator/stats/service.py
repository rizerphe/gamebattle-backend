"""Numbers for the home page and the pre-competition leaderboard message.

Only aggregates and the caller's own figures leave this module. No ids, names,
emails or per-game values are returned, and nothing here reads reports.
"""

import asyncio
import time
from dataclasses import dataclass
from uuid import UUID

from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from orchestrator.comparisons.service import Comparisons

# Counting lines reads every game's files, so the totals are reused briefly.
_PLATFORM_TTL_SECONDS = 30.0

# Sessions where a player ran their own code. Records without a kind are tests.
_OWN_RUNS = {"kind": {"$in": ["test", None]}}


@dataclass(frozen=True)
class PlatformStats:
    players: int
    runs: int
    lines: int
    largest_game_lines: int


@dataclass(frozen=True)
class CompetitionStats:
    """The caller's progress. ``elo`` and ``place`` are unset until their game is rated."""

    accumulation: int
    required_accumulation: int
    elo: float | None
    place: int | None
    places: int


@dataclass(frozen=True)
class Stats:
    platform: PlatformStats
    competition: CompetitionStats | None


def count_lines(content: bytes | None) -> int:
    """Non-blank lines."""

    if not content:
        return 0
    return sum(1 for line in content.splitlines() if line.strip())


class StatsService:
    def __init__(self, db: AsyncDatabase, comparisons: Comparisons) -> None:
        self._db = db
        self._comparisons = comparisons
        self._cached: tuple[float, PlatformStats] | None = None

    async def platform(self) -> PlatformStats:
        now = time.monotonic()
        if self._cached is not None and now < self._cached[0]:
            return self._cached[1]
        fresh = await self._platform()
        self._cached = (now + _PLATFORM_TTL_SECONDS, fresh)
        return fresh

    async def competition(
        self, player: UUID, required: int
    ) -> CompetitionStats:
        ranking = await self._comparisons.ranking()
        index = next(
            (i for i, game in enumerate(ranking) if game.game_id == player), None
        )
        return CompetitionStats(
            accumulation=await self._comparisons.records.rated_by(player),
            required_accumulation=required,
            elo=ranking[index].score if index is not None else None,
            place=index + 1 if index is not None else None,
            places=len(ranking),
        )

    async def _platform(self) -> PlatformStats:
        authors = set(await self._db.snapshots.distinct("player"))
        authors |= set(await self._db.sessions.distinct("player", _OWN_RUNS))
        runs = await self._db.sessions.count_documents(_OWN_RUNS)
        sizes = await asyncio.gather(*(self._game_lines(a) for a in sorted(authors)))
        return PlatformStats(
            players=len(authors),
            runs=runs,
            lines=sum(sizes),
            largest_game_lines=max(sizes, default=0),
        )

    async def _game_lines(self, player: UUID) -> int:
        """Lines of saved Python in the game's latest snapshot."""

        latest = await self._db.snapshots.find_one(
            {"player": player}, {"files": 1}, sort=[("number", DESCENDING)]
        )
        if latest is None:
            return 0
        return sum(
            count_lines(file.get("saved_content"))
            for file in latest.get("files", ())
            if str(file.get("path", "")).endswith(".py")
        )
