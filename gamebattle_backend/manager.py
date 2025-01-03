"""A session manager."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gamebattle_backend.game import Game

from .session import LaunchStrategy, Session

if TYPE_CHECKING:
    from .launcher import Launcher


@dataclass
class Config:
    """A configuration for the session manager."""

    max_sessions_per_user: int = 1

    @classmethod
    def default(cls) -> Config:
        """Return the default configuration."""
        return cls()


class SessionManagerError(Exception):
    """A session manager error."""


class TooManySessionsError(SessionManagerError):
    """Too many sessions for a user."""


class Manager:
    """A session manager."""

    def __init__(
        self,
        launcher: Launcher,
        config: Config | None = None,
    ) -> None:
        """Initialize the manager.

        Args:
            launcher: The launcher to use for sessions.
            config: The configuration to use.
        """
        self.sessions: dict[uuid.UUID, Session] = {}
        self.launcher = launcher
        self.config = config or Config.default()

    def get_session(self, user_id: str, session_id: uuid.UUID) -> Session:
        """Return a session.

        Args:
            user_id: The user ID.
            session_id: The session ID.

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions[session_id]
        if session.owner != user_id:
            raise KeyError
        return session

    def get_game(self, user_id: str, session_id: uuid.UUID, game_id: int) -> Game:
        """Return a game.

        Args:
            user_id: The user ID.
            session_id: The session ID.
            game_id: The game ID.

        Raises:
            KeyError: If the session or game does not exist.
        """
        return self.get_session(user_id, session_id).games[game_id]

    def user_sessions(self, user_id: str) -> dict[uuid.UUID, Session]:
        """Return a dictionary of sessions for a user.

        Args:
            user_id: The user ID.
        """
        return {
            session_id: session
            for session_id, session in self.sessions.items()
            if session.owner == user_id
        }

    async def create_session(
        self,
        owner: str,
        launch_strategy: LaunchStrategy,
        capacity: int = 2,
    ) -> tuple[uuid.UUID, Session]:
        """Create a session.

        Args:
            owner: The user ID of the session owner.
            launch_strategy: The launch strategy to use.
            capacity: The number of games to launch.
        Raises:
            TooManySessionsError: If the user already has too many sessions.
        """
        if len(self.user_sessions(owner)) >= self.config.max_sessions_per_user:
            raise TooManySessionsError
        session = await Session.launch(
            owner, self.launcher, launch_strategy, capacity=capacity
        )
        id_ = uuid.uuid4()
        self.sessions[id_] = session

        # Schedule deletion in an hour:
        async def wait_and_delete():
            await asyncio.sleep(3600)
            await self.try_stop_session(id_)

        asyncio.create_task(wait_and_delete())

        return id_, session

    async def try_stop_session(self, session_id: uuid.UUID) -> None:
        """Try to stop a session.

        Args:
            session_id: The session ID.
        """
        with contextlib.suppress(KeyError):
            await self.stop_session(session_id)

    async def stop_session(
        self, session_id: uuid.UUID, owner: str | None = None
    ) -> None:
        """Stop a session.

        Args:
            session_id: The session ID.
            owner: The user ID of the session owner.

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions[session_id]
        if owner is not None and session.owner != owner:
            raise KeyError
        await session.stop()
        del self.sessions[session_id]
