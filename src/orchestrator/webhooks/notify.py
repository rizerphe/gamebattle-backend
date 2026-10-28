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
from uuid import UUID

import httpx

from orchestrator.store import Missing, Store
from orchestrator.webhooks.models import Webhook
from orchestrator.webhooks.mongo import MongoWebhooks

logger = logging.getLogger("orchestrator.webhooks")

_TIMEOUT = 10.0
_GREEN, _YELLOW, _RED = 0x57F287, 0xFEE75C, 0xED4245


class Notifier:
    def __init__(self, store: Store, http: httpx.AsyncClient, ui_url: str | None) -> None:
        self._store = store
        self._http = http
        self._ui_url = ui_url
        self._hooks = MongoWebhooks(store.db)
        self._tasks: set[asyncio.Task[None]] = set()

    async def report_filed(
        self,
        game: UUID,
        players: int,
        new_player: bool,
        credible_players: int,
        credible: bool,
        new_credible_player: bool,
    ) -> None:
        """Tell the webhooks that asked about this report.

        ``players`` is how many different players have reported the published
        version, ``credible_players`` how many of them have a report that counts
        (judged credible by the model, or not judged at all). The first report
        and the count reached only happen when a new player reports; every
        report is sent regardless. The credible events are only for a report
        that counts.
        """

        hooks = await self._hooks.all()
        if credible:
            every_credible = [h for h in hooks if h.event == "every_credible"]
            first_credible = [
                h
                for h in hooks
                if h.event == "first_credible" and new_credible_player and credible_players == 1
            ]
            credible_reached = [
                h
                for h in hooks
                if h.event == "credible_threshold"
                and new_credible_player
                and h.threshold == credible_players
            ]
            if every_credible:
                body = await self._embed(
                    game,
                    "Game reported",
                    f"Credible reports from {_players(credible_players)} so far.",
                    _YELLOW,
                    credible_players,
                )
                self._send(every_credible, body)
            if first_credible:
                body = await self._embed(
                    game,
                    "First credible report",
                    "The published version got its first credible report.",
                    _YELLOW,
                    credible_players,
                )
                self._send(first_credible, body)
            if credible_reached:
                body = await self._embed(
                    game,
                    f"{credible_players} credible reports reached",
                    f"Credible reports from {_players(credible_players)}.",
                    _YELLOW,
                    credible_players,
                )
                self._send(credible_reached, body)
        every = [h for h in hooks if h.event == "every_report"]
        first = [h for h in hooks if h.event == "first_report" and new_player and players == 1]
        reached = [
            h for h in hooks if h.event == "threshold" and new_player and h.threshold == players
        ]
        if every:
            body = await self._embed(
                game, "Game reported", f"Reported by {_players(players)} so far.", _YELLOW, players
            )
            self._send(every, body)
        if first:
            body = await self._embed(
                game, "First report", "The published version was reported for the first time.", _YELLOW, players
            )
            self._send(first, body)
        if reached:
            body = await self._embed(
                game, f"{players} reports reached", f"Reported by {_players(players)}.", _YELLOW, players
            )
            self._send(reached, body)

    async def game_removed(self, game: UUID, count: int) -> None:
        """Tell every webhook that asked about games taken down by reports."""

        hooks = [hook for hook in await self._hooks.all() if hook.event == "removed"]
        if not hooks:
            return
        description = f"Taken down automatically after reports from {_players(count)}."
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
            embed["fields"].append({"name": "Reporters", "value": str(count), "inline": True})
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


def _players(count: int) -> str:
    return f"{count} player{'s' if count != 1 else ''}"
