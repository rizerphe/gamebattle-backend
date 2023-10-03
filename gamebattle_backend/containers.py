"""Manage game docker containers."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
import ipaddress
import socket
import time

import docker
import websockets


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
    port: int | None = None
    start_time: float = field(default_factory=time.time)
    network: str | None = None

    def __del__(self) -> None:
        """Kill the container when the object is deleted."""
        self.kill()

    @property
    def net_addr(self) -> str:
        """Own IP address and port, taking into account the docker network"""
        if self.network is None:
            return f"localhost:{self.port}"
        network = self.container.client.networks.get(self.network)
        net_address = network.attrs["Containers"][self.container.id]["IPv4Address"]
        ip = ipaddress.ip_interface(net_address).ip
        return f"{ip}:{self.port or 8080}"

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
        network: str | None = None,
        resource_limits: Limits | None = None,
    ) -> Container:
        """Starts a container for a game.

        Args:
            game (str): The name of the game
            client (docker.DockerClient): The docker client
            network (str | None): The name of the network to use
            resource_limits (Limits): The resource limits for the container

        Returns:
            Container: The container object, running the game
        """
        resource_limits = resource_limits or Limits.default()
        port = cls.pick_port()
        container = (
            client.containers.run(
                game,
                detach=True,
                network=network,
                cpu_period=50000,
                cpu_quota=int(50000 * resource_limits.cpu_fraction),
                mem_limit=f"{resource_limits.memory_mb}m",
            )
            if network
            else client.containers.run(
                game,
                detach=True,
                ports={"8080/tcp": port},
                cpu_period=50000,
                cpu_quota=int(50000 * resource_limits.cpu_fraction),
                mem_limit=f"{resource_limits.memory_mb}m",
            )
        )
        return cls(container, 8080 if network else port, network=network)

    def restart(self) -> None:
        """Restart the container."""
        self.container.restart()

    @asynccontextmanager
    async def ws(self, retries: int = 10) -> websockets.WebSocketServerProtocol:
        """Return a WebSocket stream for the game.

        Args:
            retries (int): The number of retries to do
        """
        # We do a couple retries because the server inside the container
        # might not be ready yet.
        try:
            async with websockets.connect(f"ws://{self.net_addr}/ws") as ws:
                yield ws
        except (websockets.exceptions.InvalidMessage, OSError):
            if retries > 0:
                await asyncio.sleep(1)
                async with self.ws(retries - 1) as ws:
                    yield ws
            else:
                raise

    def kill(self) -> None:
        """Kill the container."""
        if self.container.status == "running":
            self.container.kill()
        self.container.remove()
