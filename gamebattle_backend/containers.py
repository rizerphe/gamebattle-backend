"""Manage game docker containers."""
from __future__ import annotations
import asyncio
import contextlib
from dataclasses import dataclass, field
import os
import select
import socket
import threading
import time
from typing import AsyncIterator

import docker

from gamebattle_backend.mqueue import ClearableMQueue


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
    stdin: socket.socket | None = None
    output_queue: ClearableMQueue = field(default_factory=ClearableMQueue)
    receive_loop_thread: threading.Thread | None = None
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
        # Create container
        container = client.containers.run(
            game,
            detach=True,
            cpu_period=50000,
            cpu_quota=int(50000 * resource_limits.cpu_fraction),
            mem_limit=f"{resource_limits.memory_mb}m",
            stdin_open=True,
            tty=True,
            init=True,
        )
        container = cls(container)
        # Start receiving loop
        container.start_receive_loop()
        return container

    def start_receive_loop(self) -> None:
        """Start the receive loop."""
        self.receive_loop_thread = threading.Thread(
            target=self.receive_loop, daemon=True
        )
        self.receive_loop_thread.start()

    def restart(self) -> None:
        """Restart the container."""
        with self.output_queue():
            with contextlib.suppress(OSError):
                if self.stdin:
                    os.close(self.stdin.fileno())
            self.container.restart(timeout=0)

    async def send(self, message: str) -> None:
        """Send a message to the game.

        Args:
            message (str): The message to send
        """
        with self.output_queue():
            if self.stdin is None:
                return
            try:
                os.write(self.stdin.fileno(), message.encode("utf-8"))
            except OSError:
                self.stdin = None

    async def receive(self) -> AsyncIterator[str]:
        """Receive stdout from the game.

        Returns:
            str: The message received
        """
        if self.container:
            sync_receive = iter(self.output_queue)
            while True:
                chunk = await asyncio.get_event_loop().run_in_executor(
                    None, sync_receive.__next__
                )
                if chunk is None:
                    return
                yield chunk  # If an exceptions occurs, the lock will be lost

    def receive_loop(self) -> None:
        """Receive stdout from the game."""
        if self.container:
            target = self.output_queue.get_current()
            while True:
                try:
                    sock = self.stdin
                    if not self.running:
                        self.output_queue.clear()
                        return
                    if sock is None:
                        sock = self.stdin = self.container.attach_socket(
                            params={
                                "stdin": 1,
                                "stdout": 1,
                                "stderr": 1,
                                "stream": 1,
                            }
                        )
                    ready, _, _ = select.select([sock.fileno()], [], [], 5)
                    if not ready:
                        continue
                    line = os.read(sock.fileno(), 1024)
                    if line:
                        target.push(line)
                except OSError:
                    self.output_queue.clear()
                    target = self.output_queue.get_current()
                    self.stdin = None

    @property
    def running(self) -> bool:
        self.container.reload()
        return self.container.status in ["created", "running"]

    def kill(self) -> None:
        """Kill the container."""
        if self.running:
            self.container.kill("SIGKILL")
        self.container.remove()
        if self.stdin:
            with contextlib.suppress(OSError):
                os.close(self.stdin.fileno())

    def try_kill(self) -> None:
        """Try to kill the container, but don't raise any errors."""
        with contextlib.suppress(Exception):
            self.kill()
