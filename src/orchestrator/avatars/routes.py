"""Cached profile photos for linked players."""

from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from orchestrator.avatars.service import AvatarNotFound, fetch_avatar
from orchestrator.avatars.storage import AvatarStore
from orchestrator.submissions.firebase import FirebaseUnavailable

router = APIRouter()


def get_avatars(request: Request) -> AvatarStore:
    return request.app.state.avatars


def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


@router.get("/avatars/{student_id}")
async def read_avatar(student_id: UUID, request: Request) -> Response:
    settings = request.app.state.settings
    try:
        body, content_type = await fetch_avatar(
            request.app.state.store,
            get_avatars(request),
            request.app.state.users,
            get_http(request),
            student_id,
            cache_ttl=settings.avatar_cache_ttl,
        )
    except AvatarNotFound as error:
        raise HTTPException(404, "avatar not found") from error
    except FirebaseUnavailable as error:
        raise HTTPException(502, "could not reach firebase") from error
    max_age = int(settings.avatar_cache_ttl.total_seconds())
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": f"public, max-age={max_age}, immutable"},
    )
