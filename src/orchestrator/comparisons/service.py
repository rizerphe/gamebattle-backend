"""Create comparisons, replace a side, and accept a rating.

A side is finished when one of its sessions of the current game is in
``exited``. That set is filled from the terminal service: ``process-exited``
counts, and a report, restart, stop, or timeout does not. An exit stays counted
after the terminal service has forgotten the session.
"""

import asyncio
import random
import time
from collections.abc import Collection
from uuid import UUID, uuid4

from orchestrator.comparisons.models import (
    Comparison,
    ComparisonActivity,
    ComparisonPhase,
    ComparisonSideSession,
    ComparisonView,
    LeaderboardEntry,
    RankedGame,
)
from orchestrator.comparisons.mongo import MongoComparisons, MongoRatings
from orchestrator.comparisons.pairing import (
    INITIAL_ELO,
    RatedGame,
    choose_opponent,
    choose_pair,
    updated_elos,
)
from orchestrator.fairness.service import FairnessChecks
from orchestrator.fairness.verdicts import side_counts, side_is_flagged
from orchestrator.reports.models import ReportReason
from orchestrator.reports.mongo import MongoReports
from orchestrator.sessions.models import Session
from orchestrator.sessions.recording import enrich_sessions
from orchestrator.sessions.service import Sessions
from orchestrator.store import Missing, Store
from orchestrator.webhooks.notify import Notifier


class Unavailable(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Unfinished(Exception):
    pass


class AlreadyRated(Exception):
    pass


class NeedsConfirmation(Exception):
    """A side looks under-played. Rating again with ``confirmed`` goes through."""


class Comparisons:
    def __init__(
        self,
        store: Store,
        sessions: Sessions,
        rng: random.Random | None = None,
        notifier: Notifier | None = None,
        fairness: FairnessChecks | None = None,
    ):
        self.store = store
        self.sessions = sessions
        self.records = MongoComparisons(store.db)
        self.ratings = MongoRatings(store.db)
        self.reports = MongoReports(store.db, notifier)
        self.rng = rng or random.Random()
        self.fairness = fairness
        self._rebuild_lock = asyncio.Lock()

    async def start(self, owner: str) -> tuple[Comparison, bool]:
        """The caller's open comparison, or a new one. The bool is True when created."""

        player = self.store.player(owner)
        existing = await self.records.open_for(player.id)
        if existing is not None:
            return await self.observe(existing), False
        broken: set[UUID] = set()
        while True:
            candidates = await self._candidates(player.id, exclude=broken, minimum=2)
            if len(candidates) < 2:
                raise Unavailable("no games to compare")
            left, right = choose_pair(candidates, self.rng)
            snapshots = await asyncio.gather(
                self.store.runnable_snapshot(left.id),
                self.store.runnable_snapshot(right.id),
            )
            if all(snapshots):
                break
            broken.update(
                game.id for game, snapshot in zip((left, right), snapshots) if snapshot is None
            )
        first = await self.sessions.launch(owner, left.id, snapshots[0], "comparison")
        second = await self.sessions.launch(owner, right.id, snapshots[1], "comparison")
        comparison = Comparison(
            id=uuid4(),
            player=player.id,
            sessions=((first.id,), (second.id,)),
            started_at=min(first.started_at, second.started_at),
        )
        await self.records.add(comparison)
        return comparison, True

    async def get(
        self, owner: str, comparison_id: UUID, *, any_player: bool = False
    ) -> Comparison:
        return await self.observe(await self._owned(owner, comparison_id, any_player=any_player))

    async def present(self, comparison: Comparison) -> ComparisonView:
        comparison = await self.observe(comparison)
        sessions = await self._sessions(comparison)
        sides_ready = tuple(
            _side_ready(side, sessions, comparison.exited)
            for side in comparison.sessions
        )
        names: list[str | None] = []
        for side in comparison.sessions:
            session = sessions.get(side[-1]) if side else None
            if session is None:
                names.append(None)
                continue
            game = await self.store.game(session.game)
            names.append(game.name)
        return ComparisonView(
            id=comparison.id,
            player=comparison.player,
            sessions=comparison.sessions,
            rating=comparison.rating,
            sides_ready=(sides_ready[0], sides_ready[1]),
            game_names=(names[0], names[1]),
        )

    async def restart(self, owner: str, comparison_id: UUID, side: int) -> Comparison:
        comparison = await self._open(owner, comparison_id)
        previous = await self._session(comparison.sessions[side][-1])
        launched = await self.sessions.relaunch(owner, previous)
        comparison = await self.records.append(comparison.id, side, launched.id)
        await self.sessions.stop(previous.id, "restarted")
        return await self.observe(comparison)

    async def report(
        self,
        owner: str,
        comparison_id: UUID,
        side: int,
        short_reason: ReportReason,
        reason: str,
        keep_playing: bool = False,
    ) -> Comparison:
        """Report the side's game, then replace it unless the player keeps playing.

        With no game to replace it with, the report still stands and the player
        keeps playing the reported game.
        """

        comparison = await self._open(owner, comparison_id)
        previous = await self._session(comparison.sessions[side][-1])
        if previous.game == previous.player:
            raise OwnGame
        await self.reports.file(previous, short_reason, reason)
        if keep_playing:
            return await self.observe(comparison)
        other = await self._session(comparison.sessions[1 - side][-1])
        replacement = await self._replacement(comparison.player, other.game, previous.game)
        if replacement is None:
            return await self.observe(comparison)
        launched = await self.sessions.launch(
            owner,
            replacement.id,
            await self.store.published(replacement.id),
            "comparison",
            replacing=previous.id,
        )
        comparison = await self.records.append(comparison.id, side, launched.id)
        await self.sessions.stop(previous.id, "reported")
        return await self.observe(comparison)

    async def rate(
        self, owner: str, comparison_id: UUID, score: float, confirmed: bool = False
    ) -> Comparison:
        """Accept a rating once both sides have exited and their fairness checks are in.

        A side whose best session still looks under-played needs ``confirmed``
        (``NeedsConfirmation`` otherwise). Elo is then rebuilt, because whether
        a rating counts depends on those verdicts.
        """

        comparison = await self.get(owner, comparison_id)
        if comparison.abandoned:
            raise Missing(comparison_id)
        if comparison.rating is not None:
            return comparison
        sessions = await self._sessions(comparison)
        if not all(_side_ready(side, sessions, comparison.exited) for side in comparison.sessions):
            raise Unfinished
        sides = exited_sides(comparison, sessions)
        if self.fairness is not None:
            sessions.update(
                await self.fairness.settle(
                    session for side in sides for session in side
                )
            )
            sides = exited_sides(comparison, sessions)
        warned = any(side_is_flagged(side) for side in sides)
        if warned and not confirmed:
            raise NeedsConfirmation
        if not await self.records.record_rating(
            comparison.id, score, time.time(), warned and confirmed
        ):
            stored = await self.records.get(comparison.id)
            if stored is None:
                raise Missing(comparison_id)
            return stored
        await self.rebuild_ratings()
        stored = await self.records.get(comparison.id)
        if stored is None:
            raise Missing(comparison_id)
        return stored

    async def compute_ratings(
        self, exclude_players: Collection[UUID] = ()
    ) -> dict[UUID, tuple[float, int]]:
        """Elo and plays from replaying every counted rating, oldest first.

        A rating counts when, on each side, at least one exited session of the
        rated game was not judged too short. Ratings by ``exclude_players`` are skipped.
        """

        rated = await self.records.list_rated()
        sessions = await self.sessions.get_many(
            session_id
            for comparison in rated
            for side in comparison.sessions
            for session_id in side
        )
        table: dict[UUID, tuple[float, int]] = {}
        for comparison in rated:
            if comparison.player in exclude_players or comparison.rating is None:
                continue
            sides = exited_sides(comparison, sessions)
            if not all(side_counts(side) for side in sides):
                continue
            left = sessions[comparison.sessions[0][-1]].game
            right = sessions[comparison.sessions[1][-1]].game
            elo_left, plays_left = table.get(left, (INITIAL_ELO, 0))
            elo_right, plays_right = table.get(right, (INITIAL_ELO, 0))
            new_left, new_right = updated_elos(elo_left, elo_right, comparison.rating)
            table[left] = (new_left, plays_left + 1)
            table[right] = (new_right, plays_right + 1)
        return table

    async def rebuild_ratings(self) -> None:
        async with self._rebuild_lock:
            await self.ratings.replace(await self.compute_ratings())

    async def abandon_open(self) -> None:
        """Give up every unrated comparison. Their sessions are stopped separately."""

        await self.records.abandon_open()

    async def ranking(self) -> list[RankedGame]:
        """Every rated game, best first. Ids stay server-side."""

        return await self.ratings.ranking()

    async def leaderboard(self, viewer: UUID | None) -> list[LeaderboardEntry]:
        """The public view: no ids, with the viewer's own game flagged."""

        return [
            LeaderboardEntry(
                name=game.name,
                score=game.score,
                times_played=game.plays,
                own=game.game_id == viewer,
            )
            for game in await self.ranking()
        ]

    async def activity(self, player: UUID | None = None) -> list[ComparisonActivity]:
        comparisons = await self.records.list_all()
        if player is not None:
            comparisons = [row for row in comparisons if row.player == player]
        if not comparisons:
            return []
        session_ids = [
            session_id
            for comparison in comparisons
            for side in comparison.sessions
            for session_id in side
        ]
        sessions = await self.sessions.get_many(session_ids)
        player_ids = {comparison.player for comparison in comparisons} | {
            session.game for session in sessions.values()
        }
        players = await self.store.db.players.find(
            {"_id": {"$in": list(player_ids)}}, {"email": 1, "name": 1}
        ).to_list()
        emails = {
            document["_id"]: document.get("email")
            if isinstance(document.get("email"), str)
            else None
            for document in players
        }
        names = {
            document["_id"]: document.get("name")
            if isinstance(document.get("name"), str)
            else None
            for document in players
        }
        enriched_sessions = {
            session.id: session
            for session in await enrich_sessions(self.sessions.terminals, sessions.values())
        }
        entries: list[ComparisonActivity] = []
        for comparison in comparisons:
            started_at = comparison.started_at
            if started_at <= 0:
                times = [
                    sessions[session_id].started_at
                    for side in comparison.sessions
                    for session_id in side
                    if session_id in sessions
                ]
                started_at = min(times) if times else 0.0
            game_ids: list[UUID | None] = []
            game_names: list[str | None] = []
            side_sessions: list[tuple[ComparisonSideSession, ...]] = []
            for side in comparison.sessions:
                current = sessions.get(side[-1]) if side else None
                if current is None:
                    game_ids.append(None)
                    game_names.append(None)
                else:
                    game_ids.append(current.game)
                    game_names.append(names.get(current.game))
                history: list[ComparisonSideSession] = []
                for session_id in side:
                    session = sessions.get(session_id)
                    if session is None:
                        continue
                    enriched = enriched_sessions.get(session_id, session)
                    history.append(
                        ComparisonSideSession(
                            session_id=session_id,
                            game_id=session.game,
                            game_name=names.get(session.game),
                            recording_seconds=enriched.recording_seconds,
                        )
                    )
                side_sessions.append(tuple(history))
            sides_ready = tuple(
                _side_ready(side, sessions, comparison.exited)
                for side in comparison.sessions
            )
            entries.append(
                ComparisonActivity(
                    id=comparison.id,
                    player=comparison.player,
                    sessions=comparison.sessions,
                    rating=comparison.rating,
                    started_at=started_at,
                    voter_email=emails.get(comparison.player),
                    game_ids=(game_ids[0], game_ids[1]),
                    game_names=(game_names[0], game_names[1]),
                    phase=_activity_phase(comparison, sessions),
                    sides_ready=(sides_ready[0], sides_ready[1]),
                    side_sessions=(side_sessions[0], side_sessions[1]),
                )
            )
        entries.sort(key=lambda entry: (-entry.started_at, entry.id))
        return entries

    async def observe(self, comparison: Comparison) -> Comparison:
        fresh = []
        for side in comparison.sessions:
            for session_id in side:
                if session_id in comparison.exited:
                    continue
                if await self.sessions.terminals.outcome(str(session_id)) == "exited":
                    fresh.append(session_id)
        if not fresh:
            return comparison
        if self.fairness is not None:
            for session in (await self.sessions.get_many(fresh)).values():
                self.fairness.ensure(session)
        return await self.records.remember_exits(comparison.id, fresh)

    async def _open(self, owner: str, comparison_id: UUID) -> Comparison:
        comparison = await self.get(owner, comparison_id)
        if comparison.abandoned:
            raise Missing(comparison_id)
        if comparison.rating is not None:
            raise AlreadyRated
        return comparison

    async def _owned(
        self, owner: str, comparison_id: UUID, *, any_player: bool
    ) -> Comparison:
        comparison = await self.records.get(comparison_id)
        if comparison is None or not (
            any_player or comparison.player == self.store.player(owner).id
        ):
            raise Missing(comparison_id)
        return comparison

    async def _candidates(
        self, player: UUID, exclude: set[UUID], *, minimum: int
    ) -> list[RatedGame]:
        """Unseen games, or every game once the player has seen too many to fill ``minimum``."""

        unseen = await self._pool(player, exclude, await self.records.assigned_games(player))
        if len(unseen) >= minimum:
            return unseen
        return await self._pool(player, exclude, set())

    async def _pool(
        self, player: UUID, exclude: set[UUID], seen: set[UUID]
    ) -> list[RatedGame]:
        ids = await self.store.published_game_ids(seen | exclude | {player})
        table = await self.ratings.all_plays()
        return [RatedGame(game_id, *table.get(game_id, (INITIAL_ELO, 0))) for game_id in ids]

    async def _replacement(
        self, player: UUID, other: UUID, reported: UUID
    ) -> RatedGame | None:
        elo, plays = await self.ratings.get(other)
        broken: set[UUID] = set()
        while True:
            candidates = await self._candidates(
                player, exclude={other, reported} | broken, minimum=1
            )
            if not candidates:
                return None
            chosen = choose_opponent(RatedGame(other, elo, plays), candidates, self.rng)
            if await self.store.runnable_snapshot(chosen.id) is not None:
                return chosen
            broken.add(chosen.id)

    async def _session(self, session_id: UUID) -> Session:
        return await self.sessions.get(session_id)

    async def _sessions(self, comparison: Comparison) -> dict[UUID, Session]:
        ids = [session_id for side in comparison.sessions for session_id in side]
        return await self.sessions.get_many(ids)


class OwnGame(Exception):
    pass


def _activity_phase(
    comparison: Comparison, sessions: dict[UUID, Session]
) -> ComparisonPhase:
    if comparison.rating is not None:
        return "rated"
    if comparison.abandoned:
        return "abandoned"
    if all(
        _side_ready(side, sessions, comparison.exited) for side in comparison.sessions
    ):
        return "awaiting_rating"
    return "running"


def exited_sides(
    comparison: Comparison, sessions: dict[UUID, Session]
) -> tuple[list[Session], list[Session]]:
    """Per side, the sessions of the side's current game that exited on their own."""

    def pick(side: tuple[UUID, ...]) -> list[Session]:
        current = sessions[side[-1]].game
        return [
            sessions[session_id]
            for session_id in side
            if session_id in comparison.exited and sessions[session_id].game == current
        ]

    return pick(comparison.sessions[0]), pick(comparison.sessions[1])


def _side_ready(
    side: tuple[UUID, ...], sessions: dict[UUID, Session], exited: frozenset[UUID]
) -> bool:
    current = sessions[side[-1]].game
    return any(
        session_id in exited and sessions[session_id].game == current for session_id in side
    )

