"""This module contains the game class,
responsible for managing a game's metadata."""
from __future__ import annotations
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import websockets

from .common import GameMeta
from .containers import Container

if TYPE_CHECKING:
    from docker import DockerClient


@dataclass
class Game:
    """A game object, containing all the metadata"""

    metadata: GameMeta
    container: Container

    @classmethod
    def start(
        cls, meta: GameMeta, client: DockerClient, network: str | None = None
    ) -> Game:
        """Start a game.

        Args:
            meta (GameMeta): The metadata of the game
            client (DockerClient): The docker client to use
            network (str | None): The name of the network to use.
        """
        return Game(
            metadata=meta,
            container=Container.start(meta.container_name, client, network),
        )

    def restart(self) -> None:
        """Restart the game."""
        self.container.restart()

    def stop(self) -> None:
        """Stop the game."""
        self.container.kill()

    @asynccontextmanager
    async def ws(self) -> websockets.WebSocketServerProtocol:
        """Return a WebSocket stream for the game."""
        async with self.container.ws() as ws:
            yield ws
