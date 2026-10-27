"""``GET /stats``: platform totals and the caller's own competition progress."""

from fastapi import APIRouter, Request

from orchestrator.access import PhaseDep
from orchestrator.auth import Owner
from orchestrator.dependencies import StoreDep
from orchestrator.phases.models import can_see_leaderboard
from orchestrator.stats.service import Stats, StatsService

router = APIRouter()


@router.get("/stats")
async def read_stats(
    request: Request, store: StoreDep, owner: Owner, state: PhaseDep
) -> Stats:
    service: StatsService = request.app.state.stats
    competition = None
    if can_see_leaderboard(state.phase):
        competition = await service.competition(
            store.player(owner).id, request.app.state.settings.required_comparisons
        )
    return Stats(platform=await service.platform(), competition=competition)
