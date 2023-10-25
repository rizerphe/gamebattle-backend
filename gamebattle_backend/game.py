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
class GamePublic:
    """The public interface of a game."""

    name: str
    over: bool


@dataclass
class Game:
    """A game object, containing all the metadata"""

    metadata: GameMeta
    container: Container
    over: bool = False  # TODO: make this more robust to game restarts
    switching_over_allowed: bool = True

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
        self.switching_over_allowed = False
        self.container.restart()
        self.switching_over_allowed = True

    def stop(self) -> None:
        """Stop the game."""
        self.container.try_kill()

    @property
    def running(self) -> bool:
        """Return whether the game is running."""
        if self.container.running:
            return True
        if self.switching_over_allowed:
            self.over = True
        return False

    @asynccontextmanager
    async def ws(self) -> websockets.WebSocketServerProtocol:
        """Return a WebSocket stream for the game."""
        if not self.running:
            yield None
        async with self.container.ws() as ws:
            yield ws

    @property
    def public(self) -> GamePublic:
        """Return the public interface of the game."""
        return GamePublic(name=self.metadata.name, over=self.over)
