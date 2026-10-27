"""Reports over HTTP.

The player of a session files its report, which ends the session unless the
player asks to keep playing. Only admins
read reports, including the reporter's own, and replay the reported session
through ``/sessions``.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from orchestrator.auth import AdminRequired, CallerDep, Owner
from orchestrator.dependencies import NotifierDep, SessionsDep, StoreDep
from orchestrator.reports.enrich import enrich_reports
from orchestrator.reports.models import FiledReport, Report, ReportReason
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
    keep_playing: bool = False


@router.put("/sessions/{session_id}/report")
async def file_report(
    session_id: UUID,
    body: FileReport,
    store: StoreDep,
    sessions: SessionsDep,
    notifier: NotifierDep,
    owner: Owner,
) -> FiledReport:
    session = await _session(sessions, owner, session_id)
    if session.game == session.player:
        raise HTTPException(403, "you cannot report your own game")
    reason = body.reason.strip()
    if len(reason) > _MAX_REASON_LENGTH:
        raise HTTPException(400, "invalid report reason")
    report = await MongoReports(store.db, notifier).file(session, body.short_reason, reason)
    if not body.keep_playing:
        try:
            await sessions.stop(session.id, "reported")
        except TerminalError as error:
            status = error.status if error.status in (400, 404, 503) else 502
            raise HTTPException(status, error.message) from error
    return FiledReport(report.short_reason, report.reason, report.timestamp)


@router.get("/sessions/{session_id}/reports")
async def session_reports(
    session_id: UUID,
    store: StoreDep,
    sessions: SessionsDep,
    caller: CallerDep,
    _admin: AdminRequired,
) -> list[Report]:
    session = await _session(sessions, caller.owner, session_id, any_player=True)
    reports = await MongoReports(store.db).for_session(session)
    return await enrich_reports(sessions, reports)


@router.get("/reports")
async def all_reports(
    store: StoreDep,
    sessions: SessionsDep,
    _admin: AdminRequired,
    player: UUID | None = Query(default=None),
) -> list[Report]:
    reports_store = MongoReports(store.db)
    reports = (
        await reports_store.filed_by(player)
        if player is not None
        else await reports_store.all()
    )
    return await enrich_reports(sessions, reports)


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
