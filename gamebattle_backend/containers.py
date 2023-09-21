"""Manage game docker containers."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
import socket
import time

import docker
import requests
import websockets

from .common import GameOutput, Status


@dataclass
class Limits:
    """The resource limits for a container."""

    cpu_fraction: float
    memory_mb: float

    @classmethod
    def default(cls) -> Limits:
        """The default resource limits."""
        return cls(cpu_fraction=0.1, memory_mb=80)


@dataclass
class Container:
    """A class containing all the info about a container"""

    container: docker.models.containers.Container
    port: int
    start_time: float = field(default_factory=time.time)

    @property
    def logs(self) -> str:
        """The logs of the server."""
        return self.container.logs().decode("utf-8")

    @staticmethod
    def pick_port() -> int:
        """Pick a random still open port."""
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.bind(("", 0))
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            return sock.getsockname()[1]

    @classmethod
    def start(
        cls,
        game: str,
        client: docker.DockerClient,
        resource_limits: Limits | None = None,
    ) -> Container:
        """Starts a container for a game.

        Args:
            game (str): The name of the game
            client (docker.DockerClient): The docker client
            resource_limits (Limits): The resource limits for the container

        Returns:
            Container: The container object, running the game
        """
        resource_limits = resource_limits or Limits.default()
        port = cls.pick_port()
        container = client.containers.run(
            game,
            detach=True,
            ports={"8080/tcp": port},
            cpu_period=50000,
            cpu_quota=int(50000 * resource_limits.cpu_fraction),
            mem_limit=f"{resource_limits.memory_mb}m",
        )
        return cls(container, port)

    def restart(self) -> None:
        """Restart the container."""
        self.container.restart()

    def output(self) -> GameOutput:
        """Retrieve the output of the game."""
        try:
            return GameOutput(
                **requests.get(f"http://localhost:{self.port}/output", timeout=1).json()
            )
        except requests.exceptions.ConnectionError:
            return GameOutput(
                output="Game did not produce any output.",
                whole="Game did not produce any output.",
                done=True,
            )

    def kill(self) -> None:
        """Kill the container."""
        self.container.kill()

    def stdin(self, text: str) -> Status:
        """Send text to the stdin of the game.

        Args:
            text (str): The text to send

        Returns:
            Status: The status of the request
        """
        return Status(
            **requests.post(
                f"http://localhost:{self.port}/stdin",
                json=text,
                timeout=1,
            ).json()
        )

    @asynccontextmanager
    async def ws(self, retries: int = 10) -> websockets.WebSocketServerProtocol:
        """Return a WebSocket stream for the game.

        Args:
            retries (int): The number of retries to do
        """
        # We do a couple retries because the server inside the container
        # might not be ready yet.
        try:
            async with websockets.connect(f"ws://localhost:{self.port}/ws") as ws:
                yield ws
        except websockets.exceptions.InvalidMessage:
            if retries > 0:
                await asyncio.sleep(1)
                async with self.ws(retries - 1) as ws:
                    yield ws
            else:
                raise
