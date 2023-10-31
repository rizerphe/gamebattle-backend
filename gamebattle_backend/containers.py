"""Manage game docker containers."""
from __future__ import annotations
import contextlib
from dataclasses import dataclass, field
import os
import time
from typing import AsyncIterator

import docker
from docker.utils.build import tempfile

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
    stdin_path: str
    stdout_path: str
    attached: AttachedInstance | None = None
    start_time: float = field(default_factory=time.time)

    def __del__(self) -> None:
        """Kill the container when the object is deleted."""
        self.try_kill()

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
        # Create FIFO pair for stdio
        tmpdir = tempfile.mkdtemp()
        stdin_path = f"{tmpdir}/stdin"
        stdout_path = f"{tmpdir}/stdout"
        os.mkfifo(stdin_path)
        os.mkfifo(stdout_path)
        # Create container
        container = client.containers.create(
            game,
            detach=True,
            cpu_period=100000,
            cpu_quota=int(100000 * resource_limits.cpu_fraction),
            mem_limit=f"{resource_limits.memory_mb}m",
            init=True,
            mounts=[
                docker.types.Mount(
                    type="bind",
                    source=stdin_path,
                    target="/dev/game_stdin",
                    read_only=False,
                ),
                docker.types.Mount(
                    type="bind",
                    source=stdout_path,
                    target="/dev/game_stdout",
                    read_only=False,
                ),
            ],
        )
        return cls(container, stdin_path, stdout_path)

    def restart(self) -> None:
        """Restart the container."""
        if self.attached is not None:
            self.attached.close()
        self.kill()
        self.container.wait()  # Just in case
        if self.attached is not None:
            self.attached = AttachedInstance(
                self.container,
                os.open(self.stdin_path, os.O_RDWR),
                os.open(self.stdout_path, os.O_RDWR),
            )
            self.attached.start()

    async def send(self, message: str) -> None:
        """Send a message to the game.

        Args:
            message (str): The message to send
        """
        if self.attached is None:
            return
        self.attached.send(message)

    async def receive(self) -> AsyncIterator[str]:
        """Receive stdout from the game.

        Returns:
            str: The message received
        """
        if self.attached is None:
            self.attached = AttachedInstance(
                self.container,
                os.open(self.stdin_path, os.O_RDWR),
                os.open(self.stdout_path, os.O_RDWR),
            )
            self.attached.start()
        async for data in self.attached:
            yield data

    @property
    def running(self) -> bool:
        self.container.reload()
        return self.container.status in ["created", "running"]

    def kill(self) -> None:
        """Kill the container."""
        if self.running:
            self.container.kill("SIGKILL")
            if self.attached is not None:
                self.attached.close()

    def try_kill(self) -> None:
        """Try to kill the container, but don't raise any errors."""
        with contextlib.suppress(Exception):
            self.kill()
