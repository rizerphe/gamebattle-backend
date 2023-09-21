"""A session containing two competing games."""
from __future__ import annotations
from dataclasses import dataclass, field
import random
import time
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .common import GameMeta
    from .game import Game
    from .launcher import Launcher


@dataclass
class SessionPublic:
    """The public interface of a session."""

    owner: str
    launch_time: float
    games: list[str]


class LaunchStrategy(Protocol):
    """A strategy for picking N games to launch."""

    def __call__(
        self, games: list[GameMeta], capacity: int, owner: str
    ) -> list[GameMeta]:
        """Pick N games to launch.

        Args:
            games (list[GameMeta]): The games to pick from
            capacity (int): The number of games to launch
            owner (str): The owner of the session
        """


def launch_randomly(games: list[GameMeta], capacity: int, owner: str) -> list[GameMeta]:
    """Pick N games to launch.

    Args:
        games (list[GameMeta]): The games to pick from
        capacity (int): The number of games to launch
        owner (str): The owner of the session
    """
    return random.sample([game for game in games if game.author != owner], capacity)


@dataclass
class Session:
    """A session containing two competing games."""

    owner: str
    games: list[Game]
    launch_time: float = field(default_factory=time.time)

    @classmethod
    def launch(
        cls,
        owner: str,
        launcher: Launcher,
        strategy: LaunchStrategy = launch_randomly,
        capacity: int = 2,
    ) -> Session:
        """Launch a session.

        Args:
            owner (str): The owner of the session
            launcher (Launcher): The launcher to use
            strategy (LaunchStrategy): The strategy to use to pick games.
                Defaults to launch_randomly.
            capacity (int): The number of games to launch. Defaults to 2.
        """
        return Session(
            owner=owner,
            games=[
                launcher.start_game(game)
                for game in strategy(launcher.games, capacity, owner)
            ],
        )

    def stop(self) -> None:
        """Stop the session."""
        for game in self.games:
            game.stop()

    @property
    def public(self) -> SessionPublic:
        """Return a public version of the session."""
        return SessionPublic(
            owner=self.owner,
            launch_time=self.launch_time,
            games=[game.metadata.name for game in self.games],
        )
