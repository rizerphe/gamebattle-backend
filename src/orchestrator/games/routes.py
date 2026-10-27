import asyncio
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException

from orchestrator.access import EditRequired
from orchestrator.auth import AdminRequired, Owner
from orchestrator.dependencies import SessionsDep, StoreDep
from orchestrator.files.models import NoEntrypoint
from orchestrator.games.models import Game
from orchestrator.sessions.models import Session
from orchestrator.sessions.recording import enrich_session
from orchestrator.sessions.routes import _terminal_error
from orchestrator.store import Missing
from orchestrator.terminal_client import TerminalError

router = APIRouter()

_MAX_NAME_LENGTH = 80


@dataclass(frozen=True)
class SetName:
    name: str


@router.get("/game")
async def read_own(store: StoreDep, owner: Owner) -> Game:
    return await store.own_game(owner)


@router.put("/game")
async def set_name(body: SetName, store: StoreDep, owner: Owner, _: EditRequired) -> Game:
    name = body.name.strip()
    if not name or len(name) > _MAX_NAME_LENGTH:
        raise HTTPException(400, "invalid game name")
    return await store.set_game_name(owner, name)


@router.get("/games/{game_id}")
async def read_game(game_id: UUID, store: StoreDep, _admin: AdminRequired) -> Game:
    try:
        return await store.game(game_id)
    except Missing as error:
        raise HTTPException(404, "game not found") from error


@router.get("/games/{game_id}/sessions")
async def own_sessions(
    game_id: UUID,
    sessions: SessionsDep,
    _admin: AdminRequired,
) -> list[Session]:
    """The sessions where this student ran their own game, oldest first."""

    own = [
        session
        for session in await sessions.records.for_player(game_id)
        if session.kind == "test"
    ]

    async def enriched(session: Session) -> Session:
        try:
            return await enrich_session(sessions.terminals, session)
        except TerminalError:
            return session  # no recording available; listed with zero length

    return list(await asyncio.gather(*(enriched(session) for session in own)))


@router.post("/games/{game_id}/sessions", status_code=201)
async def run_latest(
    game_id: UUID,
    sessions: SessionsDep,
    owner: Owner,
    _admin: AdminRequired,
) -> Session:
    """Run this game's latest snapshot. Does not change what is published."""

    try:
        created = await sessions.create_admin_test(owner, game_id)
        return await enrich_session(sessions.terminals, created)
    except Missing as error:
        raise HTTPException(404, "game not found") from error
    except NoEntrypoint as error:
        raise HTTPException(
            400, "snapshot needs main.py, or exactly one .py file"
        ) from error
    except TerminalError as error:
        raise _terminal_error(error) from error
