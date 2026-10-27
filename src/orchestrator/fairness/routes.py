"""Admin routes for reviewing fairness verdicts."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException

from orchestrator.auth import AdminRequired
from orchestrator.comparisons.service import Comparisons
from orchestrator.dependencies import ComparisonsDep
from orchestrator.fairness import admin
from orchestrator.fairness.admin import FairnessRow, HypotheticalEntry, RatedActivity
from orchestrator.sessions.models import FairnessOverride

router = APIRouter(prefix="/admin/fairness")


@dataclass(frozen=True)
class Status:
    enabled: bool
    model: str | None


@dataclass(frozen=True)
class SetVerdict:
    verdict: FairnessOverride


@dataclass(frozen=True)
class Hypothetical:
    exclude_players: list[UUID]


@router.get("/status")
async def status(comparisons: ComparisonsDep, _admin: AdminRequired) -> Status:
    checks = comparisons.fairness
    return Status(enabled=bool(checks and checks.enabled), model=checks.model if checks else None)


@router.get("/sessions")
async def sessions(comparisons: ComparisonsDep, _admin: AdminRequired) -> list[FairnessRow]:
    return await admin.rows(comparisons)


@router.put("/sessions/{session_id}")
async def set_verdict(
    session_id: UUID, body: SetVerdict, comparisons: ComparisonsDep, _admin: AdminRequired
) -> None:
    await _override(comparisons, session_id, body.verdict)


@router.delete("/sessions/{session_id}")
async def clear_verdict(
    session_id: UUID, comparisons: ComparisonsDep, _admin: AdminRequired
) -> None:
    await _override(comparisons, session_id, None)


@router.get("/activity")
async def activity(comparisons: ComparisonsDep, _admin: AdminRequired) -> list[RatedActivity]:
    return await admin.activity(comparisons)


@router.post("/hypothetical")
async def hypothetical(
    body: Hypothetical, comparisons: ComparisonsDep, _admin: AdminRequired
) -> list[HypotheticalEntry]:
    return await admin.hypothetical(comparisons, set(body.exclude_players))


async def _override(
    comparisons: Comparisons, session_id: UUID, verdict: FairnessOverride | None
) -> None:
    if not await comparisons.sessions.records.set_override(session_id, verdict):
        raise HTTPException(404, "session not found")
    await comparisons.rebuild_ratings()
