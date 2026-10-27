"""Reports over HTTP.

The player of a session files its report, which ends the session. Only admins
read reports, including the reporter's own, and replay the reported session
through ``/sessions``.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException

from orchestrator.auth import AdminRequired, CallerDep, Owner
from orchestrator.dependencies import SessionsDep, StoreDep
from orchestrator.reports.enrich import enrich_report, enrich_reports
from orchestrator.reports.models import Report, ReportReason
from orchestrator.reports.mongo import MongoReports
from orchestrator.sessions.models import Session
from orchestrator.sessions.service import Sessions
from orchestrator.store import Missing
from orchestrator.terminal_client import TerminalError

router = APIRouter()

_MAX_REASON_LENGTH = 4000


@dataclass(frozen=True)
class FileReport:
    short_reason: ReportReason
    reason: str = ""


@router.put("/sessions/{session_id}/report")
async def file_report(
    session_id: UUID, body: FileReport, store: StoreDep, sessions: SessionsDep, owner: Owner
) -> Report:
    session = await _session(sessions, owner, session_id)
    if session.game == session.player:
        raise HTTPException(403, "you cannot report your own game")
    reason = body.reason.strip()
    if len(reason) > _MAX_REASON_LENGTH:
        raise HTTPException(400, "invalid report reason")
    report = await MongoReports(store.db).file(session, body.short_reason, reason)
    try:
        await sessions.stop(session.id, "reported")
    except TerminalError as error:
        status = error.status if error.status in (400, 404, 503) else 502
        raise HTTPException(status, error.message) from error
    return await enrich_report(sessions, report)


@router.get("/sessions/{session_id}/report")
async def read_report(
    session_id: UUID,
    store: StoreDep,
    sessions: SessionsDep,
    caller: CallerDep,
    _admin: AdminRequired,
) -> Report:
    session = await _session(sessions, caller.owner, session_id, any_player=True)
    report = await MongoReports(store.db).get(session)
    if report is None:
        raise HTTPException(404, "report not found")
    return await enrich_report(sessions, report)


@router.get("/games/{game_id}/reports")
async def game_reports(
    game_id: UUID,
    store: StoreDep,
    sessions: SessionsDep,
    _admin: AdminRequired,
) -> list[Report]:
    reports = await MongoReports(store.db).for_game(game_id)
    return await enrich_reports(sessions, reports)


async def _session(
    sessions: Sessions, owner: str, session_id: UUID, *, any_player: bool = False
) -> Session:
    try:
        return await sessions.owned(owner, session_id, any_player=any_player)
    except Missing as error:
        raise HTTPException(404, "session not found") from error
