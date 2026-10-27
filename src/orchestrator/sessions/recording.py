"""Recording length for replay links: elapsed time of the last terminal event."""

import asyncio
from collections.abc import Iterable
from dataclasses import replace

from orchestrator.sessions.models import Session
from orchestrator.terminal_client import Terminals


async def recording_seconds(terminals: Terminals, session: Session) -> float:
    events = await terminals.events(str(session.id))
    return max((event.elapsed for event in events), default=0.0)


async def enrich_session(terminals: Terminals, session: Session) -> Session:
    seconds = await recording_seconds(terminals, session)
    return replace(session, recording_seconds=seconds)


async def enrich_sessions(
    terminals: Terminals,
    sessions: Iterable[Session],
) -> list[Session]:
    items = list(sessions)
    if not items:
        return []
    return list(
        await asyncio.gather(
            *(enrich_session(terminals, session) for session in items)
        )
    )
