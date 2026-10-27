"""Closing what a phase does not allow.

A phase can end by the clock, with no request to notice it, so a background
task sweeps. The sweep is idempotent: every pass stops any open session the
current phase rejects, which also catches one launched just as the phase ended.
"""

import asyncio
import logging

from orchestrator.access import load_state
from orchestrator.comparisons.service import Comparisons
from orchestrator.phases import models
from orchestrator.phases.models import PhaseState
from orchestrator.sessions.service import Sessions
from orchestrator.store import Store

logger = logging.getLogger("orchestrator")

INTERVAL_SECONDS = 5


async def close_for_phase(
    state: PhaseState, sessions: Sessions, comparisons: Comparisons
) -> None:
    await sessions.close_where(lambda kind: models.allows_session(kind, state.phase))
    if not models.can_compare(state.phase):
        await comparisons.abandon_open()


async def sweep_forever(
    store: Store, sessions: Sessions, comparisons: Comparisons
) -> None:
    while True:
        try:
            await close_for_phase(await load_state(store), sessions, comparisons)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("phase sweep failed")
        await asyncio.sleep(INTERVAL_SECONDS)
