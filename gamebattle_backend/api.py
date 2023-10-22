"""The API server for the application."""
import asyncio
from dataclasses import dataclass
import os
from typing import Literal
import uuid

import fastapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import json
import httpx
import redis.asyncio as redis
import websockets

from gamebattle_backend.preference_store_redis import RedisPreferenceStore
from gamebattle_backend.preferences import (
    EloRatingSystem,
    Preference,
    PreferenceStore,
    Rating,
    Report,
)
from gamebattle_backend.report_store_redis import RedisReportStore

from .auth import User, verify, verify_user
from .common import GameMeta
from .launcher import Prelauncher, launch_own
from .manager import Manager, TooManySessionsError
from .session import SessionPublic


@dataclass
class PreferenceScore:
    """A preference score."""

    first_score: float


@dataclass
class File:
    """A file."""

    path: str
    content: bytes


@dataclass
class Stats:
    """The stats of an author."""

    permitted: bool
    started: bool
    elo: float


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
        preference_store: PreferenceStore,
        rating_system: EloRatingSystem,
        network: str | None,
        enable_competition: bool = True,
        report_webhook: str | None = None,
        admin_emails: list[str] | None = None,
    ) -> None:
        """Initialize the API server.

        Args:
            games_path: The path to the games directory.
            network: The docker network to use
            enable_competition: Whether to enable competition mode.
            report_webhook: The webhook to report to.
        """
        self.preference_store = preference_store
        self.rating_system = rating_system
        self.launcher = Prelauncher(
            games_path,
            network,
            prelaunch=4 if enable_competition else 0,
            prelaunch_strategy=rating_system.launch,
        )
        self.manager = Manager(self.launcher)
        self.enable_competition = enable_competition
        self.report_webhook = report_webhook
        self.admin_emails = admin_emails or []

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

    async def create_session(
        self, owner: str = fastapi.Depends(firebase_email)
    ) -> uuid.UUID:
        """Create a session.

        Args:
            owner: The user ID of the session owner.
        """
        if not self.enable_competition:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition mode is disabled."
            )
        try:
            session = await self.manager.create_session(
                owner, launch_strategy=self.rating_system.launch_preloaded
            )
            return session[0]
        except TooManySessionsError:
            raise fastapi.HTTPException(
                status_code=400, detail="Too many sessions for user."
            )

    async def create_own_session(
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
            session = await self.manager.create_session(
                owner, launch_strategy=launch_own, capacity=1
            )
            return session[0]
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
            self.manager.get_game(owner, session_id, game_id).restart()
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
            async with self.manager.ws_and_game(session_id, game_id, owner) as (
                game,
                game_socket,
            ):
                if game_socket is None:
                    await websocket.send_json({"type": "bye"})
                    await websocket.close()
                    return
                await asyncio.wait(
                    [
                        asyncio.create_task(self._ws_send(websocket, game_socket)),
                        asyncio.create_task(
                            self._ws_receive(game, websocket, game_socket)
                        ),
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
        game: "Game",
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
                await websocket.send_json({"type": "stdout", "data": message})
        except websockets.exceptions.ConnectionClosedError:
            await asyncio.sleep(0.1)
            if not game.running:
                await websocket.send_json({"type": "bye"})
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

    async def stats(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> Stats:
        """Get the stats of the user.

        Args:
            owner: The user ID of the session owner.
        """
        return Stats(
            permitted=True,
            started=self.enable_competition,
            elo=await self.rating_system.score(GameMeta.folder_name_for(owner)),
        )

    async def set_preference(
        self,
        session_id: uuid.UUID,
        score_first: float = fastapi.Body(minimum=0, maximum=1, embed=True),
        player: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Set the preference of a session.

        Args:
            session_id: The session ID.
            score_first: The score of the first game.
            player: The user ID of the session owner.
        """
        if not self.enable_competition:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition is not enabled."
            )
        try:
            session = self.manager.get_session(player, session_id)
        except KeyError:
            raise fastapi.HTTPException(status_code=404, detail="Session not found.")
        if not session.over:
            raise fastapi.HTTPException(
                status_code=400, detail="The session is not over."
            )
        preference = await Preference.from_session(session, score_first)
        await self.preference_store.set(session_id, preference)

    async def get_preference(
        self,
        session_id: uuid.UUID,
    ) -> PreferenceScore | None:
        """Get the preference of a session.

        Args:
            session_id: The session ID.
            player: The user ID of the session owner.
        """
        try:
            preference = await self.preference_store.get(session_id)
        except KeyError:
            raise fastapi.HTTPException(status_code=404, detail="Preference not found.")
        return PreferenceScore(preference.first_score) if preference else None

    async def _send_report(
        self,
        game: GameMeta,
        report: Report,
        accumulated_reports: int,
        game_name: str,
    ) -> None:
        if not self.report_webhook:
            return
        async with httpx.AsyncClient() as client:
            await client.post(
                self.report_webhook,
                json={
                    "embeds": [
                        {
                            "title": f"Game reported: {game.name}",
                            "description": report.reason,
                            "color": (0xFF0000 if report.reason else 0xFFFF00)
                            if accumulated_reports > 3
                            else 0x00FF00,
                            "fields": [
                                {
                                    "name": "Game",
                                    "value": game.name,
                                    "inline": True,
                                },
                                {
                                    "name": "Author",
                                    "value": game.author,
                                    "inline": True,
                                },
                                {
                                    "name": "Reporter",
                                    "value": report.author,
                                    "inline": True,
                                },
                                {
                                    "name": "Short reason",
                                    "value": report.short_reason,
                                    "inline": True,
                                },
                                {
                                    "name": "Logs attached",
                                    "value": "Yes" if report.output else "No",
                                    "inline": True,
                                },
                            ],
                            "footer": {
                                "text": f"Total reports: {accumulated_reports}",
                            },
                            "url": f"https://gamebattle.r1a.nl/report/{game_name}/"
                            f"{accumulated_reports}",
                        }
                    ],
                },
            )

    async def report_game(
        self,
        session_id: uuid.UUID,
        game_id: int,
        short_reason: Literal["unclear", "buggy", "other"] = fastapi.Body(...),
        reason: str = fastapi.Body(embed=True),
        output: str = fastapi.Body(embed=True),
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Report a game.

        Args:
            session_id: The session ID.
            game_id: The game ID.
            reason: The reason of the report.
            owner: The user ID of the session owner.
        """
        if not self.enable_competition:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition is not enabled."
            )
        try:
            game = self.manager.get_game(owner, session_id, game_id)
            report = Report(session_id, short_reason, reason, output, owner)
            accumulated_reports = await self.rating_system.report(game.metadata, report)
            if accumulated_reports:
                await self._send_report(
                    game.metadata,
                    report,
                    accumulated_reports,
                    game.metadata.folder_name,
                )
        except KeyError:
            raise fastapi.HTTPException(
                status_code=404, detail="Session or game not found."
            )

    async def fetch_reports(
        self,
        game_id: str,
        owner: str = fastapi.Depends(firebase_email),
    ) -> tuple[Report, ...]:
        if owner not in self.admin_emails:
            raise fastapi.HTTPException(status_code=403, detail="You are not an admin.")
        return await self.rating_system.fetch_reports(game_id)

    async def leaderboard(
        self,
    ) -> list[Rating]:
        """Get the leaderboard."""
        if not self.enable_competition:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition is not enabled."
            )
        top_game_authors: list[Rating] = []
        async for game in self.rating_system.top():
            top_game_authors.append(game)
        return top_game_authors

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

        api.on_event("startup")(self.setup)
        api.get("/sessions")(self.sessions)
        api.post("/sessions")(self.create_session)
        api.post("/sessions/own")(self.create_own_session)
        api.get("/sessions/{session_id}")(self.session)
        api.delete("/sessions/{session_id}")(self.stop_session)
        api.websocket("/sessions/{session_id}/{game_id}/ws")(self.ws)
        api.post("/sessions/{session_id}/{game_id}/restart")(self.restart_game)
        api.post("/sessions/{session_id}/{game_id}/report")(self.report_game)
        api.get("/reports/{game_id}")(self.fetch_reports)
        api.get("/sessions/{session_id}/preference")(self.get_preference)
        api.post("/sessions/{session_id}/preference")(self.set_preference)
        api.get("/leaderboard")(self.leaderboard)
        api.get("/game")(self.get_game_files)
        api.post("/game")(self.add_game_file)
        api.delete("/game/{filename:path}")(self.remove_game_file)
        api.get("/game/meta")(self.get_game_metadata)
        api.post("/game/build")(self.build_game)
        api.get("/stats")(self.stats)
        return api

    async def setup(self):
        """Setup the API server."""
        await self.launcher.prelaunch_games()
        await self.preference_store.bind(self.rating_system)


def launch_app() -> fastapi.FastAPI:
    r = redis.Redis(
        host=os.environ.get("REDIS_HOST") or "localhost",
        port=int(os.environ.get("REDIS_PORT") or 6379),
        db=int(os.environ.get("REDIS_DB") or 0),
        password=os.environ.get("REDIS_PASSWORD") or None,
    )
    return GamebattleApi(
        os.environ["GAMES_PATH"],
        RedisPreferenceStore(r),
        EloRatingSystem(RedisReportStore(r)),
        os.environ.get("NETWORK") or None,
        os.environ.get("ENABLE_COMPETITION") == "true",
        os.environ.get("REPORT_WEBHOOK") or None,
        json.loads(os.environ.get("ADMIN_EMAILS") or "[]"),
    )()
