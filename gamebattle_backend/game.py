"""This module contains the game class,
responsible for managing a game's metadata."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator

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
    client: DockerClient

    over: bool = False  # TODO: make this more robust to game restarts
    switching_over_allowed: bool = True

    @classmethod
    async def start(cls, meta: GameMeta, client: DockerClient) -> Game:
        """Start a game.

        Args:
            meta (GameMeta): The metadata of the game
            client (DockerClient): The docker client to use
        """
        container = await asyncio.get_event_loop().run_in_executor(
            None, Container.start, meta.container_name, client
        )
        return Game(metadata=meta, container=container, client=client)

    async def restart(self) -> None:
        """Restart the game."""
        self.switching_over_allowed = False
        await asyncio.get_event_loop().run_in_executor(None, self.container.kill)
        self.container = await asyncio.get_event_loop().run_in_executor(
            None, Container.start, self.metadata.container_name, self.client
        )
        self.switching_over_allowed = True

    async def stop(self) -> None:
        """Stop the game."""
        await asyncio.get_event_loop().run_in_executor(None, self.container.try_kill)

    @property
    def running(self) -> bool:
        """Return whether the game is running."""
        if self.container.running:
            return True
        if self.switching_over_allowed:
            self.over = True
        return False

    async def send(self, messsage: str) -> None:
        """Send a message to the game."""
        await self.container.send(messsage)

    async def receive(self) -> AsyncIterator[str]:
        """Receive a message from the game."""
        async for message in self.container.receive():
            yield message

    @property
    def public(self) -> GamePublic:
        """Return the public interface of the game."""
        return GamePublic(name=self.metadata.name, over=self.over)

    @property
    def accumulated_stdout(self) -> str | None:
        """Return all the accumulated stdout."""
        return self.container.accumulated_stdout
