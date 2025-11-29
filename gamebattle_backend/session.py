"""A session containing two competing games."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypeVar

from .common import GameMeta
from .game import Game

if TYPE_CHECKING:
    from .launcher import Launcher


@dataclass
class SessionPublic:
    """The public interface of a session."""

    owner: str
    launch_time: float
    games: list[dict]


LauncherType_contra = TypeVar(
    "LauncherType_contra", bound="Launcher", contravariant=True
)


class LaunchStrategy(Protocol[LauncherType_contra]):
    """A strategy for picking N games to launch."""

    async def __call__(
        self,
        launcher: LauncherType_contra,
        capacity: int,
        owner: str,
        avoid: frozenset[str] = frozenset(),
    ) -> list[GameMeta]: ...


@dataclass
class Session:
    """A session containing two competing games."""

    owner: str
    games: list[Game]
    launch_time: float = field(default_factory=time.time)

    @classmethod
    async def launch(
        cls,
        owner: str,
        launcher: LauncherType_contra,
        strategy: LaunchStrategy[LauncherType_contra],
        capacity: int = 2,
        memory_limit: int | None = None,
        cpu_limit: float | None = None,
    ) -> Session:
        """Launch a session.

        Args:
            owner (str): The owner of the session
            launcher (LauncherType_contra): The launcher to use
            strategy (LaunchStrategy): The strategy to use to pick games.
            capacity (int): The number of games to launch. Defaults to 2.
            memory_limit (int | None): Memory limit per container in bytes.
            cpu_limit (float | None): CPU limit per container as fraction of cores.
        """
        games = [
            await Game.start(game, memory_limit, cpu_limit)
            for game in await strategy(launcher, capacity, owner)
        ]
        random.shuffle(games)
        return Session(
            owner=owner,
            games=games,
        )

    async def stop(self) -> None:
        """Stop the session."""
        for game in self.games:
            await game.stop()

    @property
    def over(self) -> bool:
        """Return whether the session is over."""
        return all(game.public.over for game in self.games)

    @property
    def public(self) -> SessionPublic:
        """Return a public version of the session."""
        return SessionPublic(
            owner=self.owner,
            launch_time=self.launch_time,
            games=[game.public for game in self.games],
        )

    async def replace_game(
        self,
        game_id: int,
        owner: str,
        launcher: LauncherType_contra,
        strategy: LaunchStrategy[LauncherType_contra],
        memory_limit: int | None = None,
        cpu_limit: float | None = None,
    ) -> None:
        """Replace a game in the session.

        Args:
            game_id (int): The id of the game to replace
            owner (str): The owner of the session
            launcher (LauncherType_contra): The launcher to use
            strategy (LaunchStrategy): The strategy to use to pick a game.
            memory_limit (int | None): Memory limit per container in bytes.
            cpu_limit (float | None): CPU limit per container as fraction of cores.
        """
        await self.games[game_id].stop()
        self.games[game_id] = await Game.start(
            (
                await strategy(
                    launcher,
                    1,
                    owner,
                    avoid=frozenset(game.metadata.team_id for game in self.games),
                )
            )[0],
            memory_limit,
            cpu_limit,
        )
