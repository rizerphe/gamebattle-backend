"""Control-plane client for the terminal service.

The browser attaches to that service itself. This client only starts sessions,
stops them, and asks for tickets and checkpoints.
"""

import base64
from dataclasses import dataclass
from typing import Protocol

import httpx

from orchestrator.sessions.models import (
    RecordedEvent,
    RecordingChunk,
    TerminalEvent,
    TerminalState,
    Ticket,
)


class TerminalError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Transcript:
    """The last of what a program printed. ``skipped`` characters came before ``text``."""

    duration: float
    skipped: int
    text: str


@dataclass(frozen=True)
class Launch:
    terminal_id: str
    game_revision: str
    player: str
    started_at: float
    entrypoint: str
    files: list[tuple[str, bytes]]


class Terminals(Protocol):
    """The calls this service makes to the terminal service.

    The browser attaches to that service itself.
    """

    async def start(self, launch: Launch) -> None: ...

    async def terminate(self, terminal_id: str, reason: str) -> None: ...

    async def ticket(self, terminal_id: str) -> Ticket: ...

    async def outcome(self, terminal_id: str) -> str | None: ...

    async def recording(self, terminal_id: str, at: float) -> RecordingChunk: ...

    async def events(self, terminal_id: str) -> list[TerminalEvent]: ...

    async def transcript(self, terminal_id: str, tail: int) -> Transcript: ...


class TerminalClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, service_token: str):
        self._http = http
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {service_token}"}

    async def start(self, launch: Launch) -> None:
        response = await self._http.post(
            f"{self._base}/sessions",
            headers=self._headers,
            timeout=300,
            json={
                "session": {
                    "id": launch.terminal_id,
                    "revision": launch.game_revision,
                    "owner": launch.player,
                    "started_at": launch.started_at,
                },
                "entrypoint": launch.entrypoint,
                "columns": 80,
                "rows": 24,
                "files": [
                    {"path": path, "content": base64.b64encode(content).decode()}
                    for path, content in launch.files
                ],
            },
        )
        self._raise_for_status(response)

    async def terminate(self, terminal_id: str, reason: str) -> None:
        response = await self._http.post(
            f"{self._base}/sessions/{terminal_id}/terminate",
            headers=self._headers,
            json={"reason": reason},
        )
        if response.status_code == 404:
            return
        self._raise_for_status(response)

    async def ticket(self, terminal_id: str) -> Ticket:
        response = await self._http.post(
            f"{self._base}/sessions/{terminal_id}/tickets",
            headers=self._headers,
        )
        self._raise_for_status(response)
        issued = response.json()
        return Ticket(ticket=issued["ticket"], expires_at=issued["expires_at"])

    async def outcome(self, terminal_id: str) -> str | None:
        """How the process ended, if it has.

        ``"exited"`` means the game finished on its own. ``"terminated"`` means
        it was stopped from outside, including by a report or because the
        terminal service restarted and the container was gone. ``None`` means
        it is still running, or the terminal service has no record of it.
        """

        response = await self._http.get(
            f"{self._base}/sessions/{terminal_id}",
            headers=self._headers,
        )
        if response.status_code == 404:
            return None
        self._raise_for_status(response)
        final = response.json().get("final")
        if not isinstance(final, dict):
            return None
        if final.get("type") == "process-exited":
            return "exited"
        if final.get("type") == "process-terminated":
            return "terminated"
        return None

    async def recording(self, terminal_id: str, at: float) -> RecordingChunk:
        response = await self._http.get(
            f"{self._base}/sessions/{terminal_id}/recording",
            headers=self._headers,
            params={"at": at},
        )
        self._raise_for_status(response)
        body = response.json()
        checkpoint = body["checkpoint"]
        return RecordingChunk(
            checkpoint=TerminalState(
                sequence=checkpoint["sequence"],
                elapsed=checkpoint["elapsed"],
                type=checkpoint["type"],
                columns=checkpoint["columns"],
                rows=checkpoint["rows"],
                state=checkpoint["state"],
            ),
            events=[
                RecordedEvent(
                    sequence=event["sequence"],
                    elapsed=event["elapsed"],
                    type=event["type"],
                    data=event.get("data"),
                    columns=event.get("columns"),
                    rows=event.get("rows"),
                )
                for event in body["events"]
            ],
            next=body["next"],
        )

    async def events(self, terminal_id: str) -> list[TerminalEvent]:
        response = await self._http.get(
            f"{self._base}/sessions/{terminal_id}/events",
            headers=self._headers,
        )
        self._raise_for_status(response)
        return [
            TerminalEvent(sequence=event["sequence"], elapsed=event["elapsed"], type=event["type"])
            for event in response.json()
        ]

    async def transcript(self, terminal_id: str, tail: int) -> Transcript:
        response = await self._http.get(
            f"{self._base}/sessions/{terminal_id}/transcript",
            headers=self._headers,
            params={"tail": tail},
        )
        self._raise_for_status(response)
        body = response.json()
        return Transcript(
            duration=float(body["duration"]), skipped=int(body["skipped"]), text=body["text"]
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            message = response.json().get("error") or response.text
        except ValueError:
            message = response.text
        raise TerminalError(response.status_code, message)
