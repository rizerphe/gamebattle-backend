"""Manages the launching of containers for all the games in the project.
"""
import copy
import glob
import os
import random
import shutil
import socket
import string
import time
from contextlib import closing
from dataclasses import dataclass, field
from threading import Lock

import docker
import requests
import yaml


@dataclass
class Container:
    """A class containing all the info about a container"""

    originator: str  # The email of whoever spun it up
    game: str  # The id of the game running in the container
    data: dict  # The data of the game
    container: docker.models.containers.Container  # The container object
    port: int  # The port the game is running on
    start_time: float = field(
        default_factory=time.time
    )  # When the container was started

    @classmethod
    def start(cls, game, author, data, client):
        """Starts a container for a game."""
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            import shutil

            s.bind(("", 0))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            port = s.getsockname()[1]
        container = client.containers.run(
            game,
            detach=True,
            ports={"8080/tcp": port},
        )
        return cls(author, game, data, container, port)

    def output(self):
        """Returns the output of the game."""
        try:
            return requests.get(f"http://localhost:{self.port}/output").json()
        except requests.exceptions.ConnectionError:
            return {
                "output": "Game did not produce any output.",
                "whole": "Game did not produce any output.",
            }

    def kill(self):
        """Kills the container."""
        self.container.kill()

    def logs(self):
        """Returns the logs of the server."""
        return self.container.logs().decode("utf-8")

    def stdin(self, text):
        """Sends text to the stdin of the game."""
        return requests.post(
            f"http://localhost:{self.port}/stdin",
            data=text,
        ).json()


class Manager:
    """Manager methods for games."""

    def __init__(self):
        self.client = docker.from_env()

        self.games = {}
        self.authors = []
        self.paths = []
        self.names = []

        self.containers = []

        self.scan_games()

    def scan_games(self):
        """Scans the system for games and authors."""
        indexes = glob.glob("games/*/index.yaml")
        for index in indexes:
            with open(index, "r", encoding="utf-8") as file:
                try:
                    folder = os.path.dirname(index)
                    data = yaml.safe_load(file)
                    game = folder.split("/")[-1]

                    self.games[game] = data
                    self.authors.append(data["email"])
                    self.paths.append(folder)
                    self.names.append(game)

                    self.create_docker_context_for(folder, data, game)
                except yaml.YAMLError as exc:
                    print(exc)

    def create_docker_context_for(self, folder, game, name):
        """Creates a Docker context for a game."""
        self.create_dockerfile_for(folder, game)
        shutil.copyfile(
            "requirements_server.txt", os.path.join(folder, "requirements.txt")
        )
        shutil.copyfile("serve.py", os.path.join(folder, "serve.py"))
        self.client.images.build(path=folder, tag=name)

    def create_dockerfile_for(self, folder, game):
        """Creates a Dockerfile for a game."""
        with open(os.path.join(folder, "Dockerfile"), "w", encoding="utf-8") as file:
            file.write("""FROM python:3.10-slim\n""")
            file.write("""WORKDIR /usr/src/app\n""")
            file.write("""COPY requirements.txt ./requirements.txt\n""")
            file.write("""RUN pip install --no-cache-dir -r requirements.txt\n""")
            file.write("""RUN rm requirements.txt\n""")
            file.write("""COPY serve.py .\n""")
            file.write("""EXPOSE 8080\n""")
            file.write(f"""COPY {game["file"]} .\n""")
            file.write(f"""CMD ["python", "serve.py", "python", "{game["file"]}"]\n""")

    def start(self, game, author):
        """Starts a game."""
        container = Container.start(game, author, self.games[game], self.client)
        self.containers.append(container)
        return container

    def stdin(self, originator, game_name, text):
        """Sends text to the stdin of a game."""
        for container in self.containers:
            if container.originator == originator and container.game == game_name:
                return container.stdin(text)
        raise ValueError("No such game")

    def output(self, originator, game_name):
        """Returns the output of a game."""
        for container in self.containers:
            if container.originator == originator and container.game == game_name:
                return container.output()
        raise ValueError("No such game")

    def user_games(self, originator):
        """Returns the games a user has running."""
        return [
            container.game
            for container in self.containers
            if container.originator == originator
        ]


class HidingGameManager:
    def __init__(self, manager: Manager):
        self.manager = manager
        self.games = {}
        self.lock = Lock()

    def start(self, originator) -> str:
        with self.lock:
            candidates = copy.copy(self.manager.names)
            for game_id in self.user_games(originator):
                if self.games[game_id].game in candidates:
                    candidates.remove(self.games[game_id].game)
            if not candidates:
                candidates = copy.copy(self.manager.names)
            name = random.choice(candidates)
            game = self.manager.start(name, originator)
            id_ = "".join(random.choices(string.ascii_letters + string.digits, k=16))
            self.games[id_] = game
            return id_

    def stdin(self, game_id, text):
        return self.games[game_id].stdin(text)

    def output(self, game_id):
        return self.games[game_id].output()

    def user_games(self, originator):
        return [
            game_id
            for game_id, game in self.games.items()
            if game.originator == originator
        ]

    def kill(self, game_id):
        self.games[game_id].kill()
        del self.games[game_id]

    def restart(self, game_id):
        game = self.games[game_id]
        self.kill(game_id)
        self.games[game_id] = self.manager.start(game.game, game.originator)

    def __getitem__(self, game_id):
        return self.games.get(game_id)
