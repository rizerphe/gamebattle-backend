"""A session containing two competing games."""
from __future__ import annotations
from dataclasses import dataclass, field
import random
import time
from typing import Protocol, TypeVar

from .common import GameMeta
from .game import Game
from .launcher import Launcher, launch_randomly


@dataclass
class SessionPublic:
    """The public interface of a session."""

    owner: str
    launch_time: float
    games: list[dict]


LauncherType = TypeVar("LauncherType", bound="Launcher")


class LaunchStrategy(Protocol[LauncherType]):
    """A strategy for picking N games to launch."""

    async def __call__(
        self, launcher: LauncherType, capacity: int, owner: str
    ) -> list[GameMeta]:
        """Pick N games to launch.

        Args:
            launcher (Launcher): The launcher to use
            capacity (int): The number of games to launch
            owner (str): The owner of the session
        """


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
        launcher: LauncherType,
        strategy: LaunchStrategy[LauncherType] = launch_randomly,
        capacity: int = 2,
    ) -> Session:
        """Launch a session.

        Args:
            owner (str): The owner of the session
            launcher (LauncherType): The launcher to use
            strategy (LaunchStrategy): The strategy to use to pick games.
                Defaults to launch_randomly.
            capacity (int): The number of games to launch. Defaults to 2.
        """
        games = [
            await launcher.start_game(game)
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
        return all(game.over for game in self.games)

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
        launcher: LauncherType,
        strategy: LaunchStrategy[LauncherType] = launch_randomly,
    ) -> None:
        """Replace a game in the session.

        Args:
            game_id (int): The id of the game to replace
            owner (str): The owner of the session
            launcher (LauncherType): The launcher to use
            strategy (LaunchStrategy): The strategy to use to pick a game.
        """
        await self.games[game_id].stop()
        self.games[game_id] = await launcher.start_game(
            (await strategy(launcher, 1, owner))[0]
        )
