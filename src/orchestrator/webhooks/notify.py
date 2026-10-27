"""Send webhook notifications.

Notifications are best effort and never delay or fail the request that caused
them: each delivery runs in the background, once, with a short timeout, and a
failure is only logged. The body is a Discord embed, which other receivers that
accept Discord-compatible payloads read too. It names the game and links to the
admin report page; it never carries the report text or any recording.
"""

import asyncio
import logging
from collections.abc import Awaitable
from typing import Literal
from uuid import UUID

import httpx

from orchestrator.store import Missing, Store
from orchestrator.webhooks.models import Webhook
from orchestrator.webhooks.mongo import MongoWebhooks

logger = logging.getLogger("orchestrator.webhooks")

Cause = Literal["reports", "admin"]

_TIMEOUT = 10.0
_GREEN, _YELLOW, _RED = 0x57F287, 0xFEE75C, 0xED4245


class Notifier:
    def __init__(self, store: Store, http: httpx.AsyncClient, ui_url: str | None) -> None:
        self._store = store
        self._http = http
        self._ui_url = ui_url
        self._hooks = MongoWebhooks(store.db)
        self._tasks: set[asyncio.Task[None]] = set()

    async def reports_reached(self, game: UUID, count: int) -> None:
        """Tell every webhook with ``count`` among its thresholds."""

        hooks = [hook for hook in await self._hooks.all() if count in hook.thresholds]
        if not hooks:
            return
        body = await self._embed(
            game,
            title="Game reported",
            description=f"{count} report{'s' if count != 1 else ''} on the published version.",
            color=_YELLOW,
            count=count,
        )
        self._send(hooks, body)

    async def game_removed(self, game: UUID, cause: Cause, count: int) -> None:
        """Tell every webhook that asked about removed games."""

        hooks = [hook for hook in await self._hooks.all() if hook.on_removed]
        if not hooks:
            return
        description = (
            f"Taken down automatically after {count} reports."
            if cause == "reports"
            else "Unpublished by an admin."
        )
        self._send(hooks, await self._embed(game, "Game removed", description, _RED, count))

    async def test(self, hook: Webhook) -> None:
        """Deliver a sample message and let the caller see the failure."""

        body = {
            "embeds": [
                {
                    "title": "Gamebattle webhook test",
                    "description": "This webhook is connected.",
                    "color": _GREEN,
                }
            ]
        }
        await self._deliver(hook.url, body, raise_errors=True)

    async def _embed(
        self, game: UUID, title: str, description: str, color: int, count: int
    ) -> dict:
        try:
            name = (await self._store.game(game)).name
        except Missing:
            name = None
        label = name or "Unnamed game"
        embed: dict = {
            "title": f"{title}: {label}",
            "description": description,
            "color": color,
            "fields": [{"name": "Game", "value": label, "inline": True}],
        }
        if count:
            embed["fields"].append({"name": "Reports", "value": str(count), "inline": True})
        if self._ui_url:
            embed["url"] = f"{self._ui_url}/admin/students/{game}/reports"
        return {"embeds": [embed]}

    def _send(self, hooks: list[Webhook], body: dict) -> None:
        for hook in hooks:
            self._background(self._deliver(hook.url, body))

    def _background(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, url: str, body: dict, *, raise_errors: bool = False) -> None:
        try:
            response = await self._http.post(
                url, json=body, timeout=_TIMEOUT, follow_redirects=False
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            if raise_errors:
                raise
            logger.warning("webhook delivery failed: %s", type(error).__name__)
