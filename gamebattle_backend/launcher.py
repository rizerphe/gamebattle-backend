"""A manager for game containers."""
from __future__ import annotations
import glob
import os
import shutil

import docker
import yaml

from .common import GameMeta
from .game import Game


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
