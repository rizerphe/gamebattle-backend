"""Admin student roster: one request for games, stats, and Firebase profiles."""

from dataclasses import replace

from fastapi import APIRouter, HTTPException, Request

from orchestrator.auth import AdminRequired
from orchestrator.avatars.present import avatar_href
from orchestrator.dependencies import StoreDep
from orchestrator.submissions.firebase import FirebaseUnavailable
from orchestrator.submissions.routes import get_users
from orchestrator.submissions.service import AdminStudent, admin_students

router = APIRouter(prefix="/admin")


@router.get("/students")
async def list_admin_students(
    store: StoreDep,
    request: Request,
    _admin: AdminRequired,
) -> list[AdminStudent]:
    try:
        rows = await admin_students(
            store, get_users(request), request.app.state.settings.admin_emails
        )
    except FirebaseUnavailable as error:
        raise HTTPException(502, "could not reach firebase") from error
    return [
        replace(row, photo=avatar_href(request, row.id)) if row.photo else row
        for row in rows
    ]
