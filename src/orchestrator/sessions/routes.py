"""Sessions over HTTP.

Terminal bytes are not handled here: ``POST /sessions/{id}/tickets`` returns a
ticket, and the browser attaches to the terminal service at
``/sessions/{id}/attach?ticket=``.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException

from orchestrator.access import PhaseDep
from orchestrator.phases.models import allows_session, can_edit
from orchestrator.auth import AdminRequired, CallerDep, Owner
from orchestrator.dependencies import SessionsDep
from orchestrator.files.models import NoEntrypoint
from orchestrator.sessions.models import RecordingChunk, Session, TerminalEvent, Ticket
from orchestrator.sessions.recording import enrich_session
from orchestrator.sessions.service import PastSnapshot, Sessions, TooManySessions
from orchestrator.store import Missing
from orchestrator.terminal_client import TerminalError

router = APIRouter(prefix="/sessions")


@dataclass(frozen=True)
class CreateSession:
    game: UUID


@router.post("", status_code=201)
async def create_session(
    body: CreateSession,
    sessions: SessionsDep,
    caller: CallerDep,
    state: PhaseDep,
) -> Session:
    """Run the caller's own latest snapshot. Admins may run while editing is closed."""

    if not (can_edit(state.phase) or caller.admin):
        raise HTTPException(403, "editing is closed")
    try:
        created = await sessions.create(caller.owner, body.game)
        return await enrich_session(sessions.terminals, created)
    except Missing as error:
        raise HTTPException(404, "snapshot not found") from error
    except PastSnapshot as error:
        raise HTTPException(403, "past snapshots are not available") from error
    except NoEntrypoint as error:
        raise HTTPException(
            400, "snapshot needs main.py, or exactly one .py file"
        ) from error
    except TerminalError as error:
        raise _terminal_error(error) from error


@router.get("/{session_id}")
async def read_session(session_id: UUID, sessions: SessionsDep, caller: CallerDep) -> Session:
    session = await _owned(sessions, caller.owner, session_id, any_player=caller.admin)
    return await enrich_session(sessions.terminals, session)


@router.post("/{session_id}/restart", status_code=201)
async def restart_session(
    session_id: UUID, sessions: SessionsDep, owner: Owner, state: PhaseDep
) -> Session:
    """Launch the same snapshot again. The caller follows the new session."""

    previous = await _owned(sessions, owner, session_id)
    if not allows_session(previous.kind, state.phase):
        raise HTTPException(403, "this phase does not allow that")
    try:
        launched = await sessions.relaunch(owner, previous)
        await sessions.stop(previous.id, "restarted")
        return await enrich_session(sessions.terminals, launched)
    except Missing as error:
        raise HTTPException(404, "snapshot not found") from error
    except TooManySessions as error:
        raise HTTPException(429, "too many open sessions") from error
    except NoEntrypoint as error:
        raise HTTPException(
            400, "snapshot needs main.py, or exactly one .py file"
        ) from error
    except TerminalError as error:
        raise _terminal_error(error) from error


@router.post("/{session_id}/tickets", status_code=201)
async def ticket(session_id: UUID, sessions: SessionsDep, owner: Owner) -> Ticket:
    """Only the session's player may attach, admins included."""

    await _owned(sessions, owner, session_id)
    try:
        return await sessions.terminals.ticket(str(session_id))
    except TerminalError as error:
        raise _terminal_error(error) from error


@router.get("/{session_id}/recording")
async def recording(
    session_id: UUID, at: float, sessions: SessionsDep, caller: CallerDep, _admin: AdminRequired
) -> RecordingChunk:
    """The recorded chunk containing ``at`` seconds; the viewer replays it."""

    await _owned(sessions, caller.owner, session_id, any_player=True)
    try:
        return await sessions.terminals.recording(str(session_id), at)
    except TerminalError as error:
        raise _terminal_error(error) from error


@router.get("/{session_id}/events")
async def events(
    session_id: UUID, sessions: SessionsDep, caller: CallerDep, _admin: AdminRequired
) -> list[TerminalEvent]:
    """Every recorded terminal event, in order. The review timeline marks these."""

    await _owned(sessions, caller.owner, session_id, any_player=True)
    try:
        return await sessions.terminals.events(str(session_id))
    except TerminalError as error:
        raise _terminal_error(error) from error


async def _owned(
    sessions: Sessions, owner: str, session_id: UUID, *, any_player: bool = False
) -> Session:
    try:
        return await sessions.owned(owner, session_id, any_player=any_player)
    except Missing as error:
        raise HTTPException(404, "session not found") from error


def _terminal_error(error: TerminalError) -> HTTPException:
    status = error.status if error.status in (400, 404, 503) else 502
    return HTTPException(status, error.message)
