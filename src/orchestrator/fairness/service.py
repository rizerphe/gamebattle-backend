"""Ask the model, as soon as a comparison session exits, whether it was played enough.

One check per session, started at most once and stored on the session. A
check has a hard time limit covering the transcript fetch and the model call;
a timeout or error is stored with no verdict, which counts as ``enough``.
Without an LLM client nothing runs and no session ever gets a verdict.
"""

import asyncio
import logging
import time
from collections.abc import Iterable
from uuid import UUID

from orchestrator.files.models import entrypoint
from orchestrator.fairness import prompt
from orchestrator.llm import LlmClient
from orchestrator.sessions.models import Fairness, FairnessVerdict, Session
from orchestrator.sessions.mongo import MongoSessions
from orchestrator.store import Store
from orchestrator.terminal_client import Terminals

logger = logging.getLogger("orchestrator.fairness")

TIMEOUT_SECONDS = 3.0
TRANSCRIPT_CHARACTERS = 4000

_VERDICTS: dict[str, FairnessVerdict] = {
    "likely_enough": "enough",
    "unsure": "unsure",
    "likely_not_enough": "not_enough",
}


class FairnessChecks:
    def __init__(
        self,
        store: Store,
        terminals: Terminals,
        llm: LlmClient | None,
        timeout: float = TIMEOUT_SECONDS,
    ):
        self._store = store
        self._terminals = terminals
        self._llm = llm
        self._timeout = timeout
        self._records = MongoSessions(store.db)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    @property
    def enabled(self) -> bool:
        return self._llm is not None

    @property
    def model(self) -> str | None:
        return self._llm.model if self._llm else None

    def ensure(self, session: Session) -> asyncio.Task[None] | None:
        """Start the check unless it is done or running. None when there is nothing to wait for."""

        if self._llm is None or session.fairness is not None:
            return None
        task = self._tasks.get(session.id)
        if task is None:
            task = asyncio.create_task(self._check(session))
            self._tasks[session.id] = task
            task.add_done_callback(lambda _done: self._tasks.pop(session.id, None))
        return task

    async def settle(self, sessions: Iterable[Session]) -> dict[UUID, Session]:
        """Wait for these sessions' checks, then return them as stored."""

        sessions = list(sessions)
        tasks = [task for session in sessions if (task := self.ensure(session))]
        if tasks:
            # The request may be cancelled; the checks must still finish and be stored.
            await asyncio.gather(*(asyncio.shield(task) for task in tasks))
        return await self._records.get_many(session.id for session in sessions)

    async def _check(self, session: Session) -> None:
        assert self._llm is not None
        verdict: FairnessVerdict | None = None
        reason: str | None = None
        duration: float | None = None
        status = "done"
        try:
            async with asyncio.timeout(self._timeout):
                transcript = await self._terminals.transcript(
                    str(session.id), TRANSCRIPT_CHARACTERS
                )
                duration = transcript.duration
                snapshot = await self._store.revision(session.game, session.game_revision)
                path = entrypoint(snapshot)
                code = next(
                    file.saved_content for file in snapshot.files if file.path == path
                )
                reply = await self._llm.complete(
                    prompt.messages(
                        duration,
                        (code or b"").decode(errors="replace"),
                        transcript.text,
                        transcript.skipped,
                    ),
                    effort="low",
                    schema=prompt.SCHEMA,
                    timeout=self._timeout,
                )
                raw, reason = prompt.parse(reply)
                verdict = _VERDICTS[raw]
        except TimeoutError:
            status = "timeout"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("fairness check failed for %s: %r", session.id, error)
            status = "error"
        await self._records.set_fairness(
            session.id,
            Fairness(
                status=status,  # type: ignore[arg-type]
                verdict=verdict,
                reason=reason,
                duration=duration,
                model=self._llm.model,
                evaluated_at=time.time(),
            ),
        )
