"""The API server for the application."""
import asyncio
from dataclasses import dataclass
import os
import uuid

import fastapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import websockets

from .auth import User, verify, verify_user
from .common import GameMeta
from .launcher import Prelauncher, launch_own, launch_preloaded
from .manager import Manager, TooManySessionsError
from .session import SessionPublic


@dataclass
class File:
    """A file."""

    path: str
    content: bytes


def firebase_email(
    res: fastapi.Response,
    credential: HTTPAuthorizationCredentials = fastapi.Depends(
        HTTPBearer(auto_error=False)
    ),
) -> str:
    """A firebase dependency returning the email of the user.

    Args:
        res: The response object.
        credential: The credential object.

    Returns:
        The email of the user.
    """
    if credential is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail="Bearer authentication is needed",
            headers={"WWW-Authenticate": 'Bearer realm="auth_required"'},
        )
    email = verify(credential.credentials)
    if email is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    res.headers["WWW-Authenticate"] = 'Bearer realm="auth_required"'
    return email


def firebase_user(
    res: fastapi.Response,
    credential: HTTPAuthorizationCredentials = fastapi.Depends(
        HTTPBearer(auto_error=False)
    ),
) -> User:
    """A firebase dependency returning the user.

    Args:
        res: The response object.
        credential: The credential object.

    Returns:
        The user.
    """
    if credential is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail="Bearer authentication is needed",
            headers={"WWW-Authenticate": 'Bearer realm="auth_required"'},
        )
    user = verify_user(credential.credentials)
    if user is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    res.headers["WWW-Authenticate"] = 'Bearer realm="auth_required"'
    return user


class GamebattleApi:
    """The API server for the application."""

    def __init__(
        self,
        games_path: str,
        network: str | None,
        enable_competition: bool = True,
    ) -> None:
        """Initialize the API server.

        Args:
            games_path: The path to the games directory.
            network: The docker network to use
            enable_competition: Whether to enable competition mode.
        """
        self.launcher = Prelauncher(
            games_path, network, prelaunch=3 if enable_competition else 0
        )
        self.manager = Manager(self.launcher)
        self.enable_competition = enable_competition

    def sessions(
        self, owner: str = fastapi.Depends(firebase_email)
    ) -> dict[uuid.UUID, SessionPublic]:
        """Return a dictionary of sessions for a user.

        Args:
            owner: The user ID.
        """
        return {
            session_id: session.public
            for session_id, session in self.manager.user_sessions(owner).items()
        }

    def session(
        self,
        session_id: uuid.UUID,
        owner: str = fastapi.Depends(firebase_email),
    ) -> SessionPublic:
        """Return a session's public information.

        Args:
            session_id: The session ID.
            owner: The user ID of the session owner.
        """
        try:
            return self.manager.user_sessions(owner)[session_id].public
        except KeyError:
            raise fastapi.HTTPException(status_code=404, detail="Session not found.")

    def create_session(self, owner: str = fastapi.Depends(firebase_email)) -> uuid.UUID:
        """Create a session.

        Args:
            owner: The user ID of the session owner.
        """
        if not self.enable_competition:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition mode is disabled."
            )
        try:
            return self.manager.create_session(owner, launch_strategy=launch_preloaded)[
                0
            ]
        except TooManySessionsError:
            raise fastapi.HTTPException(
                status_code=400, detail="Too many sessions for user."
            )

    def create_own_session(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> uuid.UUID:
        """Create own session.

        Args:
            owner: The user ID of the session owner.
        """
        try:
            for session_id, session in self.manager.user_sessions(owner).items():
                if len(session.games) != 1:
                    continue
                game = session.games[0]
                if game.metadata.email == owner:
                    self.manager.stop_session(session_id, owner)
            return self.manager.create_session(
                owner, launch_strategy=launch_own, capacity=1
            )[0]
        except TooManySessionsError:
            raise fastapi.HTTPException(
                status_code=400, detail="Too many sessions for user."
            )
        except ValueError:
            raise fastapi.HTTPException(status_code=400, detail="No games available.")

    def stop_session(
        self,
        session_id: uuid.UUID,
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Stop a session.

        Args:
            session_id: The session ID.
            owner: The user ID of the session owner.
        """
        try:
            self.manager.stop_session(session_id, owner)
        except KeyError:
            raise fastapi.HTTPException(status_code=404, detail="Session not found.")

    def restart_game(
        self,
        session_id: uuid.UUID,
        game_id: int,
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Restart a game.

        Args:
            session_id: The session ID.
            game_id: The game ID.
            owner: The user ID of the session owner.
        """
        try:
            self.manager.user_sessions(owner)[session_id].games[game_id].restart()
        except KeyError:
            raise fastapi.HTTPException(
                status_code=404, detail="Session or game not found."
            )

    async def ws(
        self,
        session_id: uuid.UUID,
        game_id: int,
        websocket: fastapi.WebSocket,
    ) -> None:
        """Exchange messages with a game (authenticated).

        Args:
            session_id: The session ID.
            game_id: The game ID.
            websocket: The websocket.
            owner: The user ID of the session owner.
        """
        await websocket.accept()
        jwt = await websocket.receive_text()
        owner = verify(jwt)
        if owner is None:
            await websocket.close()
        try:
            async with self.manager.ws(session_id, game_id, owner) as game_socket:
                await asyncio.wait(
                    [
                        asyncio.create_task(self._ws_send(websocket, game_socket)),
                        asyncio.create_task(self._ws_receive(websocket, game_socket)),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
        except KeyError:
            await websocket.close()

    async def _ws_send(
        self,
        websocket: fastapi.WebSocket,
        game_socket: websockets.WebSocketClientProtocol,
    ) -> None:
        """Send messages from the websocket to the game.

        Args:
            websocket: The websocket.
            game_socket: The game's websocket.
        """
        async for message in websocket.iter_text():
            await game_socket.send(message)

    async def _ws_receive(
        self,
        websocket: fastapi.WebSocket,
        game_socket: websockets.WebSocketClientProtocol,
    ) -> None:
        """Receive messages from the game to the websocket.

        Args:
            websocket: The websocket.
            game_socket: The game's websocket.
        """
        try:
            async for message in game_socket:
                await websocket.send_text(message)
        except websockets.exceptions.ConnectionClosedError:
            await websocket.close()

    def add_game_file(
        self,
        content: bytes = fastapi.Body(...),
        filename: str = fastapi.Body(...),
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Add a game file.

        Args:
            content: The file content.
            filename: The file name.
            owner: The user ID of the session owner.
        """
        try:
            self.launcher.add_game_file(owner, content, filename)
        except ValueError:
            raise fastapi.HTTPException(
                status_code=400, detail="Invalid file name or content."
            )

    def remove_game_file(
        self,
        filename: str,
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Remove a game file.

        Args:
            filename: The file name.
            owner: The user ID of the session owner.
        """
        try:
            self.launcher.remove_game_file(owner, filename)
        except ValueError:
            raise fastapi.HTTPException(
                status_code=400, detail="Invalid file name or content."
            )

    def get_game_files(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> list[File]:
        """List game files.

        Args:
            owner: The user ID of the session owner.
        """
        return [
            File(path, content)
            for path, content in self.launcher.get_game_files(owner).items()
        ]

    def get_game_metadata(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> GameMeta | None:
        """Get game metadata.

        Args:
            owner: The user ID of the session owner.
        """
        return self.launcher.get_game_metadata(owner)

    def build_game(
        self,
        name: str = fastapi.Body(...),
        file: str = fastapi.Body(...),
        owner: User = fastapi.Depends(firebase_user),
    ) -> None:
        """Build a game.

        Args:
            name: The game name.
            file: The game entrypoint.
            owner: The user ID of the session owner.
        """
        metadata = GameMeta(name, owner.name, file, owner.email)
        self.launcher.build_game(metadata)

    def stats(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> dict[str, int]:
        """Get the stats of the user.

        Args:
            owner: The user ID of the session owner.
        """
        return {"permitted": True}

    def __call__(self) -> fastapi.FastAPI:
        """Return the API server."""
        api = fastapi.FastAPI()
        api.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        api.get("/sessions")(self.sessions)
        api.post("/sessions")(self.create_session)
        api.post("/sessions/own")(self.create_own_session)
        api.get("/sessions/{session_id}")(self.session)
        api.delete("/sessions/{session_id}")(self.stop_session)
        api.websocket("/sessions/{session_id}/{game_id}/ws")(self.ws)
        api.post("/sessions/{session_id}/{game_id}/restart")(self.restart_game)
        api.get("/game")(self.get_game_files)
        api.post("/game")(self.add_game_file)
        api.delete("/game/{filename:path}")(self.remove_game_file)
        api.get("/game/meta")(self.get_game_metadata)
        api.post("/game/build")(self.build_game)
        api.get("/stats")(self.stats)
        return api


def launch_app() -> fastapi.FastAPI:
    return GamebattleApi(
        os.environ["GAMES_PATH"],
        os.environ.get("NETWORK") or None,
        os.environ.get("ENABLE_COMPETITION") == "true",
    )()
