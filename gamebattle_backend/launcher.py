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
        self, games_path: str, requirements_path: str, server_path: str
    ) -> None:
        """Initialize the manager.

        Args:
            games_path (str): The path to the games folder
            requirements_path (str): The path to the server requirements file
            server_path (str): The path to the server python executable
        """
        self.client = docker.from_env()
        self.games_path = games_path
        self.requirements_path = requirements_path
        self.server_path = server_path

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
        shutil.copyfile(
            self.requirements_path, os.path.join(folder, "requirements.txt")
        )
        shutil.copyfile(self.server_path, os.path.join(folder, "launch.py"))
        self.client.images.build(path=folder, tag=game.container_name)

    def create_dockerfile_for(self, folder: str, game: GameMeta) -> None:
        """Creates a Dockerfile for a game.

        Args:
            folder (str): The folder of the game
            game (GameMeta): The metadata of the game
        """
        with open(os.path.join(folder, "Dockerfile"), "w", encoding="utf-8") as file:
            file.write("""FROM python:3.11-slim\n""")
            file.write("""WORKDIR /usr/src/app\n""")
            file.write("""COPY requirements.txt ./requirements.txt\n""")
            file.write("""RUN pip install --no-cache-dir -r requirements.txt\n""")
            file.write("""RUN rm requirements.txt\n""")
            file.write("""COPY launch.py .\n""")
            file.write("""EXPOSE 8080\n""")
            file.write(f"""COPY {game.file} .\n""")
            file.write(f"ENV COMMAND python {game.file}\n")
            file.write(
                f"""CMD ["uvicorn", "launch:launch", "--host", "0.0.0.0", "--port", "8080", "--factory"]\n"""
            )

    def start_game(self, meta: GameMeta) -> Game:
        """Start a game.

        Args:
            meta (GameMeta): The metadata of the game

        Returns:
            Game: The started game
        """
        return Game.start(meta, self.client)
