"""A manager for game containers."""
from __future__ import annotations
import glob
import os
import random
from typing import TYPE_CHECKING

import docker
import yaml

from .common import GameMeta
from .game import Game

if TYPE_CHECKING:
    from .session import LaunchStrategy


def launch_randomly(launcher: Launcher, capacity: int, owner: str) -> list[GameMeta]:
    """Pick N games to launch.

    Args:
        launcher (Launcher): The launcher to use
        capacity (int): The number of games to launch
        owner (str): The owner of the session
    """
    return random.sample(
        [game for game in launcher.games if game.author != owner], capacity
    )


def launch_preloaded(
    launcher: Prelauncher, capacity: int, owner: str
) -> list[GameMeta]:
    """Pick N games to launch, preferring prelaunched games.

    Args:
        launcher (Prelauncher): The launcher to use
        capacity (int): The number of games to launch
        owner (str): The owner of the session
    """
    available = [game for game in launcher.prelaunched if game.author != owner]
    if len(available) < capacity:
        available.extend(
            random.sample(
                [game for game in launcher.games if game.author != owner],
                capacity - len(available),
            )
        )
    return random.sample(available, capacity)


class Launcher:
    """A launcher for game containers."""

    def __init__(
        self,
        games_path: str,
        network: str | None = None,
    ) -> None:
        """Initialize the manager.

        Args:
            games_path (str): The path to the games folder
            network (str | None): The name of the network to use.
        """
        self.client = docker.from_env()
        self.games_path = games_path
        self.network = network

        self.games = self.scan_games()

    def scan_games(self) -> list[GameMeta]:
        """Scan the games folder for games."""
        indexes = glob.glob(os.path.join(self.games_path, "*/index.yaml"))
        games: list[GameMeta] = []
        for index in indexes:
            with open(index, "r", encoding="utf-8") as file:
                folder = os.path.dirname(index)
                data = yaml.safe_load(file)
                game = GameMeta(**data)
                games.append(game)

                self.create_docker_context_for(folder, game)
        return games

    def create_docker_context_for(self, folder: str, game: GameMeta) -> None:
        """Creates a Docker context for a game.

        Args:
            folder (str): The folder of the game
            game (GameMeta): The metadata of the game
        """
        self.create_dockerfile_for(folder, game)
        self.client.images.build(path=folder, tag=game.container_name)

    def create_dockerfile_for(self, folder: str, game: GameMeta) -> None:
        """Creates a Dockerfile for a game.

        Args:
            folder (str): The folder of the game
            game (GameMeta): The metadata of the game
        """
        with open(os.path.join(folder, "Dockerfile"), "w", encoding="utf-8") as file:
            file.write("""FROM rizerphe/gamebattle-launcher:latest\n""")
            file.write(f"""COPY {game.file} .\n""")
            file.write(f"ENV COMMAND python {game.file}\n")

    def start_game(self, meta: GameMeta) -> Game:
        """Start a game.

        Args:
            meta (GameMeta): The metadata of the game

        Returns:
            Game: The started game
        """
        return Game.start(meta, self.client, self.network)


class Prelauncher(Launcher):
    """A launcher for game containers that keeps a couple running just in case"""

    def __init__(
        self,
        games_path: str,
        network: str | None = None,
        prelaunch: int = 3,
        prelaunch_strategy: LaunchStrategy = launch_randomly,
    ) -> None:
        """Initialize the manager.

        Args:
            games_path (str): The path to the games folder
            network (str | None): The name of the network to use.
            prelaunch (int): The number of games to keep running
        """
        super().__init__(games_path, network)
        self.prelaunch = prelaunch
        self.prelaunched: dict[GameMeta, list[Game]] = {}
        self.prelaunch_strategy = prelaunch_strategy
        self.prelaunch_games()

    def prelaunch_games(self) -> None:
        """Prelaunches games."""
        for _ in range(len(self.prelaunched), self.prelaunch):
            meta = random.choice(self.games)
            game = super().start_game(meta)
            self.prelaunched.setdefault(meta, []).append(game)

    def start_game(self, meta: GameMeta) -> Game:
        """Start a game.

        Args:
            meta (GameMeta): The metadata of the game

        Returns:
            Game: The started game
        """
        game = (
            self.prelaunched[meta].pop()
            if meta in self.prelaunched and self.prelaunched[meta]
            else super().start_game(meta)
        )
        self.prelaunch_games()
        return game
