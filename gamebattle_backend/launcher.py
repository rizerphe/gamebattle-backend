"""A manager for game containers."""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import string
from dataclasses import asdict

import yaml

from .builder import GameBuilder
from .common import GameMeta, TeamManager
from .session import LaunchStrategy
from .summarize import Summarizer


class GamebattleError(Exception):
    """Raised when a file upload fails."""

    def __init__(self, message: str) -> None:
        """Initialize the error.

        Args:
            message (str): The message of the error
        """
        super().__init__(message)
        self.message = message


async def launch_randomly(
    launcher: Launcher, capacity: int, owner: str, avoid: frozenset[str] = frozenset()
) -> list[GameMeta]:
    """Pick N games to launch.

    Args:
        launcher (Launcher): The launcher to use
        capacity (int): The number of games to launch
        owner (str): The owner of the session (the email)
    """
    available = [
        game
        for game in launcher.games
        if not launcher.allowed_access(game, owner) and game.team_id not in avoid
    ]
    return available and random.sample(available, capacity)


async def launch_own(
    launcher: Launcher, capacity: int, owner: str, avoid: frozenset[str] = frozenset()
) -> list[GameMeta]:
    """Launch user's own games.

    Raises:
        GamebattleError: If the user requests more than one game or if the user has no games.

    Args:
        launcher (Launcher): The launcher to use
        capacity (int): The number of games to launch
        owner (str): The owner of the session
    """
    if capacity > 1:
        raise GamebattleError("Can only own one game at a time")
    available = [
        game for game in launcher.games if launcher.allowed_access(game, owner)
    ]
    if not available:
        raise GamebattleError("No games available")
    return random.sample(available, capacity)


def launch_specified(game_id: str) -> LaunchStrategy:
    async def launch(
        launcher: Launcher,
        capacity: int,
        owner: str,
        avoid: frozenset[str] = frozenset(),
    ) -> list[GameMeta]:
        return [launcher[game_id]]

    return launch


class Launcher:
    """A launcher for game containers."""

    def __init__(
        self,
        games_path: str,
        teams: TeamManager,
    ) -> None:
        self._games_path = games_path
        self._summarizer = Summarizer()
        self._builder = GameBuilder(games_path)
        self._teams = teams

        self.games: list[GameMeta] = []

    def allowed_access(self, game: GameMeta, owner: str) -> bool:
        """Check if the owner has access to the game.

        Args:
            game (GameMeta): The game
            owner (str): The owner of the session

        Returns:
            bool: Whether the owner has access to the game
        """
        return game.allowed_access(owner, self._teams)

    async def start(self) -> None:
        """Scan the games folder for games."""
        self.games = await self._builder.scan()

    def filename_component_valid(self, component: str, strict: bool = False) -> bool:
        """Check if a file name component is valid.

        Args:
            component (str): The component of the file name
            strict (bool): Whether to be strict about the file name component

        Returns:
            bool: Whether the file name component is valid
        """
        if len(component) > 255:
            return False
        if len(component) == 0:
            return False
        allowed_chars = (
            string.ascii_uppercase
            + string.ascii_lowercase
            + string.digits
            + "_-."
            + ("" if strict else "  ")
        )
        if not all(char in allowed_chars for char in component):
            return False
        required_chars = (
            string.ascii_uppercase + string.ascii_lowercase + string.digits + "_-"
        )
        return any(char in required_chars for char in component)

    def check_file_name(self, filename: str, strict: bool = False) -> bool:
        """Check if a file name is valid.

        Args:
            filename (str): The name of the file
            strict (bool): Whether to be strict about the file name

        Returns:
            bool: Whether the file name is valid
        """
        components = filename.split("/")
        if len(components) > 10:
            return False
        return all(
            self.filename_component_valid(component, strict) for component in components
        )

    async def build_game(self, metadata: GameMeta) -> None:
        if not self.check_file_name(metadata.file, strict=True):
            return
        if not metadata.team_id:
            return
        self.save_metadata(metadata)
        await self._builder.build(metadata)
        self.games = [x for x in self.games if x.team_id != metadata.team_id] + [
            metadata
        ]

    def __getitem__(self, team_id: str, /) -> GameMeta:
        for game in self.games:
            if game.team_id == team_id:
                return game
        raise KeyError(team_id)

    def add_game_file(
        self, team_id: str, game_file_content: bytes, filename: str
    ) -> None:
        """Add a game file to the manager.

        Args:
            team_id (str): The ID of the team
            game_file_content (str): The content of the game file
            filename (str): The name of the file
        """
        if not self.check_file_name(filename):
            raise GamebattleError("Invalid file name")
        if len(game_file_content) > 128 * 1024:
            raise GamebattleError("File too large")
        if len(self.get_game_files(team_id)) > 64:
            raise GamebattleError("Too many files")

        # Recursively create the folder (including the sections of the file's path)
        os.makedirs(
            os.path.join(
                self._games_path,
                team_id,
                os.path.dirname(filename),
            ),
            exist_ok=True,
        )

        # Write the game file:
        with open(
            os.path.join(self._games_path, team_id, filename),
            "wb",
        ) as file:
            file.write(game_file_content)

    def remove_game_file(self, team_id: str, filename: str) -> None:
        """Delete a game file from the manager.

        Args:
            team_id (str): The ID of the team
            filename (str): The name of the file
        """
        if not self.check_file_name(filename):
            raise GamebattleError("Invalid file name")
        try:
            os.remove(os.path.join(self._games_path, team_id, filename))
            # And recursively remove all empty dirs
            for root, _, _ in os.walk(os.path.join(self._games_path, team_id)):
                if not os.listdir(root):
                    os.rmdir(root)
        except FileNotFoundError:
            pass

    def get_game_files(self, team_id: str) -> dict[str, bytes]:
        """Recursively get the game files of a game.

        Args:
            team_id (str): The ID of the team

        Returns:
            dict[str, str]: The game files
        """
        files: dict[str, bytes] = {}
        for root, _, filenames in os.walk(
            os.path.join(self._games_path, team_id),
        ):
            for filename in filenames:
                with open(os.path.join(root, filename), "rb") as file:
                    relative_path = os.path.relpath(
                        os.path.join(root, filename),
                        os.path.join(self._games_path, team_id),
                    )
                    files[relative_path] = file.read()
        return files

    async def get_game_summary(self, team_id: str) -> str:
        """Get an one-line AI-generated summary of a game.

        Args:
            team_id (str): The ID of the team
        """
        # Get the entrypoint file
        files = self.get_game_files(team_id)
        try:
            metadata = self[team_id]
        except KeyError:
            return "Get started by creating a game"
        if metadata.file not in files:
            return "Time to specify the entrypoint file!"
        file_content = files.get(metadata.file, b"").decode("utf-8", errors="ignore")

        return await self._summarizer.summarize(file_content)

    def save_metadata(self, metadata: GameMeta) -> None:
        """Save the metadata of a game.

        Args:
            metadata (GameMeta): The metadata of the game
        """
        with open(
            os.path.join(self._games_path, metadata.team_id + ".yaml"),
            "w",
            encoding="utf-8",
        ) as file:
            yaml.safe_dump(asdict(metadata), file)

    def __contains__(self, team_id: str) -> bool:
        return any(game.team_id == team_id for game in self.games)

    async def start_generating_summaries(self):
        """Start generating summaries."""
        asyncio.create_task(self._generate_summaries())

    async def _generate_summaries(self):
        """Generate summaries for all games continuously,
        one game per minute, as they are updated.
        """
        print("Starting to generate summaries", flush=True)
        while True:
            teams = [game.team_id for game in self.games]
            random.shuffle(teams)

            # Find first game that needs a summary
            for team_id in teams:
                files = self.get_game_files(team_id)
                try:
                    metadata = self[team_id]
                except KeyError:
                    continue
                if metadata.file not in files:
                    continue
                file_content = files.get(metadata.file, b"").decode(
                    "utf-8", errors="ignore"
                )

                if self._summarizer.will_summary_exist(file_content):
                    continue

                with contextlib.suppress(Exception):
                    print(
                        f"Generating summary for {metadata.team_id}'s game {metadata.name}",
                        flush=True,
                    )
                    await self._summarizer.summarize(file_content, strong=False)
                    break

            await asyncio.sleep(60)
