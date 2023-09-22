"""A session manager."""
from __future__ import annotations
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING
import uuid

import websockets

from .session import LaunchStrategy, Session, launch_randomly

if TYPE_CHECKING:
    from .launcher import Launcher
    from .common import GameOutput


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
        launch_strategy: LaunchStrategy = launch_randomly,
    ) -> None:
        """Initialize the manager.

        Args:
            launcher: The launcher to use for sessions.
            config: The configuration to use.
        """
        self.sessions: dict[uuid.UUID, Session] = {}
        self.launcher = launcher
        self.launch_strategy = launch_strategy
        self.config = config or Config.default()
        self.lock = RLock()

    def user_sessions(self, user_id: str) -> dict[uuid.UUID, Session]:
        """Return a dictionary of sessions for a user.

        Args:
            user_id: The user ID.
        """
        with self.lock:
            return {
                session_id: session
                for session_id, session in self.sessions.items()
                if session.owner == user_id
            }

    def create_session(self, owner: str) -> tuple[uuid.UUID, Session]:
        """Create a session.

        Args:
            owner: The user ID of the session owner.

        Raises:
            TooManySessionsError: If the user already has too many sessions.
        """
        with self.lock:
            if len(self.user_sessions(owner)) >= self.config.max_sessions_per_user:
                raise TooManySessionsError
            session = Session.launch(owner, self.launcher, self.launch_strategy)
            id_ = uuid.uuid4()
            self.sessions[id_] = session
            return id_, session

    def stop_session(self, session_id: uuid.UUID, owner: str | None = None) -> None:
        """Stop a session.

        Args:
            session_id: The session ID.
            owner: The user ID of the session owner.

        Raises:
            KeyError: If the session does not exist.
        """
        with self.lock:
            session = self.sessions[session_id]
            if owner is not None and session.owner != owner:
                raise KeyError
            session.stop()
            del self.sessions[session_id]

    def send(
        self, session_id: uuid.UUID, game_id: int, text: str, owner: str | None = None
    ) -> None:
        """Send a message to a session.

        Args:
            session_id: The session ID.
            game_id: The game ID.
            text: The text to send.
        """
        session = self.sessions[session_id]
        if owner is None or session.owner == owner:
            session.games[game_id].send(text)

    def receive(
        self, session_id: uuid.UUID, game_id: int, owner: str | None = None
    ) -> GameOutput | None:
        """Receive a message from a session.

        Args:
            session_id: The session ID.
            game_id: The game ID.
            owner: The user ID of the session owner.
        """
        session = self.sessions[session_id]
        if owner is None or session.owner == owner:
            return session.games[game_id].receive()
        return None

    @asynccontextmanager
    async def ws(
        self, session_id: uuid.UUID, game_id: int, owner: str | None = None
    ) -> websockets.WebSocketServerProtocol:
        """Return a WebSocket stream for a session.

        Args:
            session_id: The session ID.
            game_id: The game ID.
            owner: The user ID of the session owner.

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions[session_id]
        if owner is None or session.owner == owner:
            async with session.games[game_id].ws() as ws:
                yield ws
                return
        raise KeyError
