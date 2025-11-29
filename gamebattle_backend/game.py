"""This module contains the game class,
responsible for managing a game's metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from .common import GameMeta
from .container import Container


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

    @classmethod
    async def start(
        cls,
        meta: GameMeta,
        memory_limit: int | None = None,
        cpu_limit: float | None = None,
    ) -> Game:
        """Start a new game with the given metadata."""
        container = Container(meta.image_name, memory_limit, cpu_limit)
        await container.start()
        return cls(meta, container)

    async def restart(
        self,
        memory_limit: int | None = None,
        cpu_limit: float | None = None,
    ) -> None:
        await self.container.stop()
        self.container = Container(self.metadata.image_name, memory_limit, cpu_limit)
        await self.container.start()

    async def stop(self) -> None:
        """Stop the game."""
        await self.container.stop()

    async def send(self, messsage: bytes) -> None:
        """Send a message to the game."""
        await self.container.send(messsage)

    async def resize(self, width: int, height: int) -> None:
        """Resize the game."""
        await self.container.resize(width, height)

    async def receive(self) -> AsyncIterator[bytes]:
        """Receive messages from the game."""
        async for message in self.container.receive():
            yield message.content

    @property
    def accumulated_output(self) -> bytes:
        """The accumulated output of the game."""
        return b"".join(
            output.content for output in self.container.receive().accumulated
        )

    @property
    def public(self) -> GamePublic:
        """The public interface of the game."""
        return GamePublic(self.metadata.name, not self.container.running)
