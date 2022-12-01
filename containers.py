"""Manages the launching of containers for all the games in the project.
"""
import glob
import os
import time
from dataclasses import dataclass, field

import docker
import yaml


@dataclass
class Container:
    """A class containing all the info about a container"""

    originator: str  # The email of whoever spun it up
    game: str  # The id of the game running in the container
    container: docker.models.containers.Container  # The container object
    start_time: float = field(
        default_factory=time.time
    )  # When the container was started
    stdins: list[tuple[int, str]] = field(
        default_factory=list
    )  # A list of stdin events

    @classmethod
    def start(cls, game, author, client):
        """Starts a container for a game."""
        container = client.containers.run(
            game,
            detach=True,
            mem_limit="16m",
            stdin_open=True,
        )
        return cls(author, game, container)

    def send_stdin(self, text):
        """Sends text to the container's stdin."""
        self.stdins.append((len(self.output()), text))

        socket = self.container.attach_socket(params={"stdin": 1, "stream": 1})
        socket._sock.send(text.encode("utf-8"))
        socket.close()

    def kill(self):
        """Kills the container."""
        self.container.kill()

    def logs(self):
        """Returns the logs of the container."""
        return self.container.logs().decode("utf-8")

    def output(self):
        """Returns the output of the container."""
        text = self.logs()
        for stdin in self.stdins:
            text = text[: stdin[0]] + stdin[1] + text[stdin[0] :]
        return text


class Manager:
    """Manager methods for games."""

    def __init__(self):
        self.client = docker.from_env()

        self.games = []
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

                    self.games.append(data)
                    self.authors.append(data["email"])
                    self.paths.append(folder)
                    self.names.append(game)

                    self.create_dockerfile_for(folder, data)
                    self.client.images.build(path=folder, tag=game)
                except yaml.YAMLError as exc:
                    print(exc)

    def create_dockerfile_for(self, folder, game):
        """Creates a Dockerfile for a game."""
        with open(os.path.join(folder, "Dockerfile"), "w", encoding="utf-8") as file:
            file.write("""FROM python:3.10-slim\n""")
            file.write("""WORKDIR /usr/src/app\n""")
            file.write(f"""COPY {game["file"]} .\n""")
            file.write(f"""CMD ["python", "-u", "{game["file"]}"]\n""")
