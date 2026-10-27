"""Launch sessions and record that they exist.

The terminal service owns the event log. This records, in MongoDB, whose
session it is and which snapshot it runs, and it starts and stops the terminal.
"""

import asyncio
import time
from collections import defaultdict
from collections.abc import Iterable
from uuid import UUID, uuid4

from orchestrator.files.models import Snapshot, entrypoint, saved_files
from orchestrator.sessions.models import Session, SessionKind
from orchestrator.sessions.mongo import MongoSessions
from orchestrator.store import Missing, Store
from orchestrator.terminal_client import Launch, TerminalError, Terminals


class PastSnapshot(Exception):
    """A player may launch only their latest snapshot."""


class TooManySessions(Exception):
    """A player already has every session they may have open."""


# Open sessions one player may have at once, per kind. A new ``test`` or
# ``admin`` session replaces the old one; a comparison has exactly two sides.
MAX_OPEN: dict[SessionKind, int] = {"test": 1, "admin": 1, "comparison": 2}

# The terminal service stops every session at its time limit (three hours).
# A session this old is closed even when the terminal service has forgotten it.
_STALE_AFTER = 3 * 60 * 60 + 5 * 60


class Sessions:
    def __init__(self, store: Store, terminals: Terminals):
        self.store = store
        self.terminals = terminals
        self.records = MongoSessions(store.db)
        self._locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def owned(
        self, owner: str, session_id: UUID, *, any_player: bool = False
    ) -> Session:
        """The owner's session. ``any_player`` also allows everyone else's."""

        session = await self.records.get(session_id)
        if session is None or not (any_player or session.player == self.store.player(owner).id):
            raise Missing(session_id)
        return session

    async def get(self, session_id: UUID) -> Session:
        session = await self.records.get(session_id)
        if session is None:
            raise Missing(session_id)
        return session

    async def get_many(self, session_ids: Iterable[UUID]) -> dict[UUID, Session]:
        return await self.records.get_many(session_ids)

    async def create(self, owner: str, game: UUID) -> Session:
        """Launch the latest snapshot. This does not publish."""

        player = self.store.player(owner)
        current = await player.files.get_snapshot("current")
        try:
            snapshot = await player.files.get_snapshot(game)
        except KeyError as error:
            raise Missing(game) from error
        if snapshot.id != current.id:
            raise PastSnapshot(game)
        return await self.launch(owner, player.id, snapshot, "test")

    async def create_admin_test(self, owner: str, game_id: UUID) -> Session:
        """Launch another game's latest snapshot for an admin to play."""

        files = await self.store.game_files(game_id)
        snapshot = await files.get_snapshot("current")
        return await self.launch(owner, game_id, snapshot, "admin")

    async def relaunch(self, owner: str, previous: Session) -> Session:
        """Launch the same snapshot again.

        This does not stop ``previous``. The caller stops it after recording
        the new session, so a comparison can append the new session first.
        """

        snapshot = await self.store.revision(previous.game, previous.game_revision)
        return await self.launch(
            owner, previous.game, snapshot, previous.kind, replacing=previous.id
        )

    async def launch(
        self,
        owner: str,
        game: UUID,
        snapshot: Snapshot,
        kind: SessionKind,
        replacing: UUID | None = None,
    ) -> Session:
        """Launch a snapshot of ``game`` for ``owner`` to play.

        ``replacing`` is a session the caller is about to stop, which does not
        count against the player's limit.
        """

        player = self.store.player(owner)
        async with self._locks[player.id]:
            await self._make_room(player.id, kind, replacing)
            session = Session(
                id=uuid4(),
                game=game,
                game_revision=snapshot.id,
                player=player.id,
                started_at=time.time(),
                kind=kind,
            )
            await self.terminals.start(
                Launch(
                    terminal_id=str(session.id),
                    game_revision=str(snapshot.id),
                    player=str(player.id),
                    started_at=session.started_at,
                    entrypoint=entrypoint(snapshot),
                    files=saved_files(snapshot),
                )
            )
            await self.records.add(session)
            return session

    async def open_sessions(self, player: UUID) -> list[Session]:
        """The player's sessions that are still running. Finished ones are closed."""

        now = time.time()
        running = []
        for session in await self.records.unclosed_for_player(player):
            if (
                now - session.started_at > _STALE_AFTER
                or await self.terminals.outcome(str(session.id)) is not None
            ):
                await self.records.close(session.id, now)
            else:
                running.append(session)
        return running

    async def _make_room(
        self, player: UUID, kind: SessionKind, replacing: UUID | None
    ) -> None:
        open_of_kind = [
            session
            for session in await self.open_sessions(player)
            if session.kind == kind and session.id != replacing
        ]
        excess = len(open_of_kind) - (MAX_OPEN[kind] - 1)
        if excess <= 0:
            return
        if kind == "comparison":
            raise TooManySessions
        for session in open_of_kind[:excess]:
            await self.stop(session.id, "replaced")

    async def stop(self, session_id: UUID, reason: str) -> None:
        await self.terminals.terminate(str(session_id), reason)
        await self.records.close(session_id, time.time())

    async def close_where(self, allowed) -> int:
        """Stop every open session whose kind ``allowed`` rejects.

        Sessions the terminal service cannot reach stay open and are tried
        again on the next call.
        """

        kinds = [kind for kind in MAX_OPEN if not allowed(kind)]
        if not kinds:
            return 0
        stopped = 0
        for session in await self.records.unclosed_of_kinds(kinds):
            try:
                await self.stop(session.id, "the phase changed")
            except TerminalError:
                continue
            stopped += 1
        return stopped
