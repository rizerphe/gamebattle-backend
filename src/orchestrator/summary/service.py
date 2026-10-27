"""The home-page sentence about a player's game.

The first request for a version of the code waits for the model. Every later
request returns the line made ahead of time and starts the next one, so each
visit shows something new. Lines live in memory, keyed by player and code.
Anything that goes wrong, or no LLM at all, means no line and the caller shows
its placeholder.
"""

import asyncio
import hashlib
import logging
import random
from uuid import UUID

from orchestrator.fairness.prompt import trim_code
from orchestrator.llm import LlmClient, LlmError
from orchestrator.summary.words import WORDS

logger = logging.getLogger("orchestrator.summary")

TIMEOUT_SECONDS = 10.0
_MAX_CACHED = 1000

_INSTRUCTION = (
    "Write a single mildly (not obviously) cat-themed short sentence that's either:"
    "\n- a lighthearted joke on a not-yet-done part of the game"
    "\n- a quick suggestion on what to do next"
    "\n- a witty description of the game.\n"
)
_EXAMPLE_CODE = 'print("Hello, world!")'
_EXAMPLE_ANSWER = (
    "The game will eventually be amazing, as soon as you stop being a sleepy cat!"
)


def _messages(code: str, words: list[str]) -> list[dict[str, str]]:
    def ask(source: str, extra: str = "") -> str:
        return (
            "Here's the prototype code for what's eventually supposed to become a "
            f"console game:\n```python\n{source}\n```\n\n{_INSTRUCTION}{extra}"
            "Respond with just the sentence, nothing else."
        )

    inspiration = (
        f"For inspiration, loosely work in the feel of: {', '.join(words)}.\n"
    )
    return [
        {"role": "user", "content": ask(_EXAMPLE_CODE)},
        {"role": "assistant", "content": _EXAMPLE_ANSWER},
        {"role": "user", "content": ask(trim_code(code), inspiration)},
    ]


class Summaries:
    def __init__(self, llm: LlmClient | None, timeout: float = TIMEOUT_SECONDS):
        self._llm = llm
        self._timeout = timeout
        self._lines: dict[tuple[UUID, str], str] = {}
        self._tasks: dict[tuple[UUID, str], asyncio.Task[str | None]] = {}

    async def line(self, player: UUID, code: str) -> str | None:
        if self._llm is None:
            return None
        key = (player, hashlib.sha256(code.encode()).hexdigest())
        ready = self._lines.pop(key, None)
        if ready is not None:
            self._refresh(key, code)
            return ready
        try:
            async with asyncio.timeout(self._timeout):
                line = await asyncio.shield(self._start(key, code))
        except TimeoutError:
            return None
        if line is not None:
            self._refresh(key, code)
        return line

    def _start(self, key: tuple[UUID, str], code: str) -> asyncio.Task[str | None]:
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.create_task(self._generate(code))
            self._tasks[key] = task
            task.add_done_callback(lambda _done: self._tasks.pop(key, None))
        return task

    def _refresh(self, key: tuple[UUID, str], code: str) -> None:
        def store(done: asyncio.Task[str | None]) -> None:
            line = None if done.cancelled() else done.result()
            if line is None:
                return
            if len(self._lines) >= _MAX_CACHED:
                self._lines.pop(next(iter(self._lines)))
            self._lines[key] = line

        self._start(key, code).add_done_callback(store)

    async def _generate(self, code: str) -> str | None:
        assert self._llm is not None
        try:
            reply = await self._llm.complete(
                _messages(code, random.sample(WORDS, 5)),
                effort="medium",
                timeout=self._timeout,
            )
        except LlmError as error:
            logger.warning("summary failed: %s", error)
            return None
        return " ".join(reply.strip().strip('"').split()) or None
