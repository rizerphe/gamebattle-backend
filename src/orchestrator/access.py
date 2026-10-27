"""What each phase allows.

Editing a game, playing a comparison, and seeing the leaderboard all follow the
single current phase. Build and test follows editing: it launches the latest
snapshot and does not publish. Only admins publish or unpublish, and the phase
does not restrict admin routes.
"""

import time
from typing import Annotated

from fastapi import Depends, HTTPException

from orchestrator.dependencies import StoreDep
from orchestrator.phases import models
from orchestrator.phases.models import PhaseState
from orchestrator.phases.mongo import MongoPhases


async def load_state(store: StoreDep) -> PhaseState:
    schedule = await MongoPhases(store.db).schedule()
    return PhaseState(models.current_phase(schedule, time.time()), schedule)


PhaseDep = Annotated[PhaseState, Depends(load_state)]


def require_edit(state: PhaseDep) -> None:
    if not models.can_edit(state.phase):
        raise HTTPException(403, "editing is closed")


def require_compare(state: PhaseDep) -> None:
    if not models.can_compare(state.phase):
        raise HTTPException(403, "comparisons are closed")


def require_leaderboard(state: PhaseDep) -> None:
    if not models.can_see_leaderboard(state.phase):
        raise HTTPException(403, "leaderboard is closed")


EditRequired = Annotated[None, Depends(require_edit)]
CompareRequired = Annotated[None, Depends(require_compare)]
LeaderboardRequired = Annotated[None, Depends(require_leaderboard)]
