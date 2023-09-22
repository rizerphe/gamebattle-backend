"""The API server for the application."""
import asyncio
import os
import uuid

import fastapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import websockets

from .auth import verify
from .common import GameOutput
from .launcher import Prelauncher, launch_preloaded
from .manager import Manager, TooManySessionsError
from .session import SessionPublic


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


class GamebattleApi:
    """The API server for the application."""

    def __init__(
        self,
        games_path: str,
        network: str | None,
    ) -> None:
        """Initialize the API server.

        Args:
            games_path: The path to the games directory.
            network: The docker network to use
        """
        self.manager = Manager(
            Prelauncher(games_path, network), launch_strategy=launch_preloaded
        )

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
        try:
            return self.manager.create_session(owner)[0]
        except TooManySessionsError:
            raise fastapi.HTTPException(
                status_code=400, detail="Too many sessions for user."
            )

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

    def send(
        self,
        session_id: uuid.UUID,
        game_id: int,
        text: str = fastapi.Body(...),
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Send a message to a game.

        Args:
            session_id: The session ID.
            game_id: The game ID.
            text: The text to send.
            owner: The user ID of the session owner.
        """
        try:
            self.manager.send(session_id, game_id, text, owner)
        except KeyError:
            raise fastapi.HTTPException(status_code=404, detail="Session not found.")

    def receive(
        self,
        session_id: uuid.UUID,
        game_id: int,
        owner: str = fastapi.Depends(firebase_email),
    ) -> GameOutput:
        """Receive the output from a game (non-authenticated).

        Args:
            session_id: The session ID.
            game_id: The game ID.
            owner: The user ID of the session owner.
        """
        try:
            output = self.manager.receive(session_id, game_id, owner)
            if output is None:
                raise fastapi.HTTPException(
                    status_code=404, detail="Session not found."
                )
            return output
        except KeyError:
            raise fastapi.HTTPException(status_code=404, detail="Session not found.")

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
            try:
                await game_socket.send(message)
            except (
                websockets.ConnectionClosedError,
                websockets.ConnectionClosedOK,
                fastapi.websockets.WebSocketDisconnect,
            ):
                websocket.close()

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
        async for message in game_socket:
            await websocket.send_text(message)

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
        api.get("/sessions/{session_id}")(self.session)
        api.delete("/sessions/{session_id}")(self.stop_session)
        api.post("/sessions/{session_id}/{game_id}/send")(self.send)
        api.get("/sessions/{session_id}/{game_id}/receive")(self.receive)
        api.websocket("/sessions/{session_id}/{game_id}/ws")(self.ws)
        return api


def launch_app() -> fastapi.FastAPI:
    return GamebattleApi(
        os.environ["GAMES_PATH"],
        os.environ.get("NETWORK") or None,
    )()
