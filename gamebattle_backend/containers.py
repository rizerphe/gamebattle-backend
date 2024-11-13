"""Manage game docker containers."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

import docker

from gamebattle_backend.container_attached import AttachedInstance


@dataclass
class Limits:
    """The resource limits for a container."""

    cpu_fraction: float
    memory_mb: float

    @classmethod
    def default(cls) -> Limits:
        """The default resource limits."""
        return cls(cpu_fraction=0.1, memory_mb=40)


@dataclass
class Container:
    """A class containing all the info about a container"""

    container: docker.models.containers.Container
    attached: AttachedInstance | None = None
    start_time: float = field(default_factory=time.time)

    def __del__(self) -> None:
        """Kill the container when the object is deleted."""
        if self.running:
            with contextlib.suppress(Exception):
                self.container.kill("SIGKILL")

    @classmethod
    async def start(
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
        # Create container
        print(f"Starting container for {game}", flush=True)
        container = client.containers.create(
            game,
            detach=True,
            cpu_period=100000,
            cpu_quota=int(100000 * resource_limits.cpu_fraction),
            mem_limit=f"{resource_limits.memory_mb}m",
            init=True,
            stdin_open=True,
            tty=True,
        )
        print(f"Started container for {game}", flush=True)
        return cls(container)

    async def send(self, message: str) -> None:
        """Send a message to the game.

        Args:
            message (str): The message to send
        """
        if self.attached is None:
            self.attached = AttachedInstance(
                self.container,
            )
            await self.attached.start()
        await self.attached.send(message)

    async def resize(self, width: int, height: int) -> None:
        """Resize the container.

        Args:
            width (int): The new width
            height (int): The new height
        """
        if self.attached is None:
            self.attached = AttachedInstance(
                self.container,
            )
            await self.attached.start()
        await self.attached.resize(width, height)

    async def receive(self) -> AsyncIterator[str]:
        """Receive stdout from the game.

        Returns:
            str: The message received
        """
        if self.attached is None:
            self.attached = AttachedInstance(
                self.container,
            )
            await self.attached.start()
        async for data in self.attached:
            yield data

    @property
    def accumulated_stdout(self) -> str | None:
        """Return all the accumulated stdout."""
        return (
            None
            if self.attached is None
            else self.attached.data.decode("utf-8", errors="ignore")
        )

    @property
    def running(self) -> bool:
        self.container.reload()
        return self.container.status in ["created", "running"]

    async def kill(self) -> None:
        """Kill the container."""
        if self.running:
            self.container.kill("SIGKILL")
            if self.attached is not None:
                await self.attached.close()

    async def try_kill(self) -> None:
        """Try to kill the container, but don't raise any errors."""
        with contextlib.suppress(Exception):
            async with asyncio.timeout(5):
                await self.kill()
