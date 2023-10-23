"""This module contains the game class,
responsible for managing a game's metadata."""
from __future__ import annotations
from dataclasses import dataclass
from typing import AsyncIterator, TYPE_CHECKING

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
    def start(cls, meta: GameMeta, client: DockerClient) -> Game:
        """Start a game.

        Args:
            meta (GameMeta): The metadata of the game
            client (DockerClient): The docker client to use
        """
        return Game(
            metadata=meta,
            container=Container.start(meta.container_name, client),
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
