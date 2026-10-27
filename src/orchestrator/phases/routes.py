"""The phase over HTTP.

``GET /phase`` is public. Admins set when each later phase starts.
"""

import math
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException

from orchestrator.access import PhaseDep, load_state
from orchestrator.auth import AdminRequired
from orchestrator.dependencies import ComparisonsDep, SessionsDep, StoreDep
from orchestrator.phases.models import Phase, PhaseState
from orchestrator.phases.mongo import MongoPhases
from orchestrator.phases.sweep import close_for_phase

router = APIRouter()


@dataclass(frozen=True)
class SetStart:
    at: float | None


@router.get("/phase")
async def read_phase(state: PhaseDep) -> PhaseState:
    # Public: the UI reads the phase before anyone has signed in.
    return state


@router.put("/phase/schedule/{phase}")
async def set_start(
    phase: Phase,
    body: SetStart,
    store: StoreDep,
    sessions: SessionsDep,
    comparisons: ComparisonsDep,
    _admin: AdminRequired,
) -> PhaseState:
    if phase == Phase.DEVELOPMENT:
        raise HTTPException(400, "development has no start")
    if body.at is not None and not math.isfinite(body.at):
        raise HTTPException(400, "invalid time")
    phases = MongoPhases(store.db)
    schedule = (await phases.schedule()).with_start(phase, body.at)
    if not schedule.in_order():
        raise HTTPException(400, "phases must start in order")
    await phases.set_start(phase, body.at)
    state = await load_state(store)
    await close_for_phase(state, sessions, comparisons)
    return state

