"""A manager for game containers."""
from __future__ import annotations
from dataclasses import asdict
import glob
import os
import random
import string
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
    available = [game for game in launcher.games if game.email != owner]
    return available and random.sample(available, capacity)


def launch_preloaded(
    launcher: Prelauncher, capacity: int, owner: str
) -> list[GameMeta]:
    """Pick N games to launch, preferring prelaunched games.

    Args:
        launcher (Prelauncher): The launcher to use
        capacity (int): The number of games to launch
        owner (str): The owner of the session
    """
    available = [game for game in launcher.prelaunched if game.email != owner]
    if len(available) < capacity:
        selection = [game for game in launcher.games if game.email != owner]
        if len(selection) < capacity:
            return []
        available.extend(
            random.sample(
                selection,
                capacity - len(available),
            )
        )
    return available and random.sample(available, capacity)


def launch_own(launcher: Launcher, capacity: int, owner: str) -> list[GameMeta]:
    """Launch user's own games.

    Raises:
        ValueError: If the user requests more than one game or if the user has no games.

    Args:
        launcher (Launcher): The launcher to use
        capacity (int): The number of games to launch
        owner (str): The owner of the session
    """
    if capacity > 1:
        raise ValueError("Can only own one game at a time")
    available = [game for game in launcher.games if game.email == owner]
    if not available:
        raise ValueError("No games available")
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
        indexes = glob.glob(os.path.join(self.games_path, "*.yaml"))
        games: list[GameMeta] = []
        for index in indexes:
            with open(index, "r", encoding="utf-8") as file:
                data = yaml.safe_load(file)
                game = GameMeta(**data)
                games.append(game)

                self.create_docker_context_for(game)
        return games

    def create_docker_context_for(self, game: GameMeta) -> None:
        """Creates a Docker context for a game.

        Args:
            folder (str): The folder of the game
            game (GameMeta): The metadata of the game
        """
        self.create_dockerfile_for(game)
        self.client.images.build(
            path=os.path.join(self.games_path, game.folder_name),
            dockerfile=f"../{game.folder_name}.Dockerfile",
            tag=game.container_name,
        )

    def create_dockerfile_for(self, game: GameMeta) -> None:
        """Creates a Dockerfile for a game.

        Args:
            game (GameMeta): The metadata of the game
        """
        with open(
            os.path.join(self.games_path, f"{game.folder_name}.Dockerfile"),
            "w",
            encoding="utf-8",
        ) as file:
            file.write("""FROM rizerphe/gamebattle-launcher:latest\n""")
            file.write("""COPY . .\n""")
            file.write(f"ENV COMMAND python {game.file}\n")

    def start_game(self, meta: GameMeta) -> Game:
        """Start a game.

        Args:
            meta (GameMeta): The metadata of the game

        Returns:
            Game: The started game
        """
        return Game.start(meta, self.client, self.network)

    def filename_component_valid(self, component: str) -> bool:
        """Check if a file name component is valid.

        Args:
            component (str): The component of the file name

        Returns:
            bool: Whether the file name component is valid
        """
        if len(component) > 255:
            return False
        if len(component) == 0:
            return False
        allowed_chars = (
            string.ascii_uppercase + string.ascii_lowercase + string.digits + "_- ."
        )
        if not all(char in allowed_chars for char in component):
            return False
        required_chars = (
            string.ascii_uppercase + string.ascii_lowercase + string.digits + "_-"
        )
        return any(char in required_chars for char in component)

    def check_file_name(self, filename: str) -> bool:
        """Check if a file name is valid.

        Args:
            filename (str): The name of the file

        Returns:
            bool: Whether the file name is valid
        """
        components = filename.split("/")
        if len(components) > 10:
            return False
        return all(self.filename_component_valid(component) for component in components)

    def add_game_file(
        self, owner: str, game_file_content: bytes, filename: str
    ) -> None:
        """Add a game file to the manager.

        Args:
            owner (str): The owner of the game
            game_file_content (str): The content of the game file
            filename (str): The name of the file
        """
        if not self.check_file_name(filename):
            raise ValueError("Invalid file name")
        if len(game_file_content) > 128 * 1024:
            raise ValueError("File too large")
        if len(self.get_game_files(owner)) > 64:
            raise ValueError("Too many files")

        # Recursively create the folder (including the sections of the file's path)
        os.makedirs(
            os.path.join(
                self.games_path,
                GameMeta.folder_name_for(owner),
                os.path.dirname(filename),
            ),
            exist_ok=True,
        )

        # Write the game file:
        with open(
            os.path.join(self.games_path, GameMeta.folder_name_for(owner), filename),
            "wb",
        ) as file:
            file.write(game_file_content)

    def remove_game_file(self, owner: str, filename: str) -> None:
        """Delete a game file from the manager.

        Args:
            owner (str): The owner of the game
            filename (str): The name of the file
        """
        if not self.check_file_name(filename):
            raise ValueError("Invalid file name")
        try:
            os.remove(
                os.path.join(self.games_path, GameMeta.folder_name_for(owner), filename)
            )
            # And recursively remove all empty dirs
            for root, _, _ in os.walk(
                os.path.join(self.games_path, GameMeta.folder_name_for(owner))
            ):
                if not os.listdir(root):
                    os.rmdir(root)
        except FileNotFoundError:
            pass

    def get_game_files(self, owner: str) -> dict[str, bytes]:
        """Recursively get the game files of a game.

        Args:
            owner (str): The owner of the game

        Returns:
            dict[str, str]: The game files
        """
        files: dict[str, bytes] = {}
        for root, _, filenames in os.walk(
            os.path.join(self.games_path, GameMeta.folder_name_for(owner))
        ):
            for filename in filenames:
                with open(os.path.join(root, filename), "rb") as file:
                    relative_path = os.path.relpath(
                        os.path.join(root, filename),
                        os.path.join(self.games_path, GameMeta.folder_name_for(owner)),
                    )
                    files[relative_path] = file.read()
        return files

    def get_game_metadata(self, owner: str) -> GameMeta | None:
        """Get the metadata of a game.

        Args:
            owner (str): The owner of the game

        Returns:
            GameMeta: The metadata of the game
        """
        found = [x for x in self.games if x.email == owner]
        if len(found) == 0:
            return None
        return found[0]

    def save_metadata(self, metadata: GameMeta) -> None:
        """Save the metadata of a game.

        Args:
            metadata (GameMeta): The metadata of the game
        """
        with open(
            os.path.join(self.games_path, metadata.folder_name + ".yaml"),
            "w",
            encoding="utf-8",
        ) as file:
            yaml.dump(asdict(metadata), file)

    def build_game(self, metadata: GameMeta) -> None:
        """Add a game to the manager.

        Args:
            metadata (GameMeta): The metadata of the game
        """
        self.save_metadata(metadata)
        self.create_docker_context_for(metadata)
        self.games.append(metadata)

    def exists_game(self, metadata: GameMeta) -> bool:
        """Check if a game exists.

        Args:
            metadata (GameMeta): The metadata of the game
        """
        return metadata in self.games


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
        if self.games:
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

    def build_game(self, metadata: GameMeta) -> None:
        """Add a game to the manager.

        Args:
            metadata (GameMeta): The metadata of the game
        """
        if metadata in self.prelaunched:
            del self.prelaunched[metadata]
        return super().build_game(metadata)
