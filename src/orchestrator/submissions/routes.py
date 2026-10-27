"""Admin import of a finished game archive, and the Firebase profile of a player.

One request submits and publishes every game in the zip. Each address is handed
to Firebase as written; the account id that comes back is the only identity
stored. Games Firebase does not recognize are still imported, and listed in
``unlinked``. ``GET /students`` reads the name and photo of every linked
player, and ``GET /students/{id}`` reads them for one UUID.
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from orchestrator.auth import AdminRequired
from orchestrator.avatars.present import present_profile
from orchestrator.dependencies import PlayerDep, StoreDep
from orchestrator.submissions.archive import MAX_ARCHIVE_BYTES, ArchiveRejected
from orchestrator.submissions.firebase import (
    FirebaseUnavailable,
    UserDirectory,
)
from orchestrator.submissions.service import (
    MissingStudent,
    StudentProfile,
    SubmissionResult,
    lookup_students,
    student_profile,
    submit_archive,
)

router = APIRouter()


@dataclass(frozen=True)
class StudentLookup:
    ids: list[UUID]


def get_users(request: Request) -> UserDirectory:
    return request.app.state.users


@router.post("/submissions")
async def submit_games(
    store: StoreDep,
    actor: PlayerDep,
    _admin: AdminRequired,
    request: Request,
    archive: Annotated[UploadFile, File()],
) -> SubmissionResult:
    blob = await archive.read(MAX_ARCHIVE_BYTES + 1)
    if len(blob) > MAX_ARCHIVE_BYTES:
        raise HTTPException(400, "archive is too large")
    try:
        return await submit_archive(
            store, blob, author=actor.id, users=get_users(request)
        )
    except ArchiveRejected as error:
        raise HTTPException(400, error.detail) from error
    except FirebaseUnavailable as error:
        raise HTTPException(502, "could not reach firebase") from error


@router.post("/students/lookup")
async def resolve_students(
    body: StudentLookup,
    store: StoreDep,
    _admin: AdminRequired,
    request: Request,
) -> list[StudentProfile]:
    try:
        profiles = await lookup_students(store, get_users(request), body.ids)
        return [present_profile(request, profile) for profile in profiles]
    except FirebaseUnavailable as error:
        raise HTTPException(502, "could not reach firebase") from error


@router.get("/students/{student_id}")
async def read_student(
    student_id: UUID,
    store: StoreDep,
    _admin: AdminRequired,
    request: Request,
) -> StudentProfile:
    try:
        profile = await student_profile(store, get_users(request), student_id)
        return present_profile(request, profile)
    except MissingStudent as error:
        raise HTTPException(404, "student not found") from error
    except FirebaseUnavailable as error:
        raise HTTPException(502, "could not reach firebase") from error
