"""How student profiles expose cached avatar URLs."""

from dataclasses import replace
from uuid import UUID

from fastapi import Request

from orchestrator.submissions.service import StudentProfile


def avatar_href(request: Request, student_id: UUID) -> str:
    base = str(request.base_url)
    return f"{base}avatars/{student_id}"


def present_profile(request: Request, profile: StudentProfile) -> StudentProfile:
    if profile.photo is None:
        return profile
    return replace(profile, photo=avatar_href(request, profile.id))
