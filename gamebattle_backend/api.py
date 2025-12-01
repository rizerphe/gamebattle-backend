"""The API server for the application."""

import asyncio
import base64
import contextlib
import csv
import os
import uuid
from dataclasses import dataclass
from io import StringIO
from typing import Coroutine, Literal

import fastapi
import httpx
import redis.asyncio as redis
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import json

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
from .common import GameMeta, TeamManager
from .game import Game
from .launcher import GamebattleError, Launcher, launch_own, launch_specified
from .manager import Manager, TooManyContainersError, TooManySessionsError
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
    elo: float | None
    max_elo: float
    place: int | None
    places: int | None
    accumulation: float
    required_accumulation: float
    reports: int
    times_played: int
    game_name: str | None


@dataclass
class EloChange:
    """ELO change for a game."""

    team_id: str
    before: float
    after: float


@dataclass
class PreferenceHistoryEntry:
    """A preference history entry with ELO changes."""

    games: tuple[str, str]
    first_score: float
    author: str
    timestamp: float
    elo_changes: list[EloChange]


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
        teams_path: str,
        autobuild_teams: bool,
        preference_store: PreferenceStore,
        rating_system: EloRatingSystem,
        enable_competition: bool = True,
        report_webhook: str | None = None,
        admin_emails: list[str] | None = None,
    ) -> None:
        """Initialize the API server.

        Args:
            games_path: The path to the games directory.
            enable_competition: Whether to enable competition mode.
            report_webhook: The webhook to report to.
        """
        self.preference_store = preference_store
        self.rating_system = rating_system
        self.teams_path = teams_path
        self.teams = TeamManager(autobuild_teams)
        self.launcher = Launcher(
            games_path,
            self.teams,
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
                owner, launch_strategy=self.rating_system.launch
            )
            return session[0]
        except TooManySessionsError:
            raise fastapi.HTTPException(
                status_code=400, detail="Too many sessions for user."
            )
        except TooManyContainersError:
            raise fastapi.HTTPException(
                status_code=503, detail="Server is at capacity. Try again later."
            )

    async def create_own_session(
        self,
        game_id: str | None = fastapi.Body(None, embed=True),
        owner: str = fastapi.Depends(firebase_email),
    ) -> uuid.UUID:
        """Create own session.

        Args:
            game_id: The id of the game to launch (admin only)
            owner: The user ID of the session owner.
        """
        if self.enable_competition and owner not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition mode is disabled."
            )
        if game_id is not None and owner not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Cannot specify game ID."
            )
        try:
            for session_id, session in self.manager.user_sessions(owner).items():
                if len(session.games) != 1 and owner not in self.admin_emails:
                    continue
                await self.manager.stop_session(session_id, owner)
            session_id, session = await self.manager.create_session(
                owner,
                launch_strategy=(
                    launch_own if game_id is None else launch_specified(game_id)
                ),
                capacity=1,
            )
            return session_id
        except TooManySessionsError:
            raise fastapi.HTTPException(
                status_code=400, detail="Too many sessions for user."
            )
        except TooManyContainersError:
            raise fastapi.HTTPException(
                status_code=503, detail="Server is at capacity. Try again later."
            )
        except GamebattleError as e:
            raise fastapi.HTTPException(status_code=400, detail=e.message)

    async def stop_session(
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
            await self.manager.stop_session(session_id, owner)
        except KeyError:
            raise fastapi.HTTPException(status_code=404, detail="Session not found.")

    async def restart_game(
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
            await self.manager.get_game(owner, session_id, game_id).restart(
                self.manager.config.container_memory_limit,
                self.manager.config.container_cpu_limit,
            )
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
            await websocket.send_json({"type": "bye"})
            await websocket.close()
            return
        try:
            game = self.manager.get_game(owner, session_id, game_id)
            await asyncio.wait(
                [
                    asyncio.create_task(self._ws_send(websocket, game)),
                    asyncio.create_task(self._ws_receive(game, websocket)),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
        except KeyError:
            await websocket.send_json({"type": "bye"})
            await websocket.close()

    async def _ws_send(
        self,
        websocket: fastapi.WebSocket,
        game: Game,
    ) -> None:
        """Send messages from the websocket to the game.

        Args:
            websocket: The websocket.
            game_socket: The game's websocket.
        """
        async for message in websocket.iter_text():
            with contextlib.suppress(json.JSONDecodeError):
                message = json.loads(message)
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "stdin":
                    data = message.get("data")
                    if not isinstance(data, str):
                        continue
                    # B64-decode the received data:
                    await game.send(base64.b64decode(data))
                elif message.get("type") == "resize":
                    rows = message.get("rows")
                    cols = message.get("cols")
                    if not isinstance(rows, int) or not isinstance(cols, int):
                        continue
                    await game.resize(cols, rows)

    async def _ws_receive(
        self,
        game: Game,
        websocket: fastapi.WebSocket,
    ) -> None:
        """Receive messages from the game to the websocket.

        Args:
            websocket: The websocket.
            game_socket: The game's websocket.
        """
        async for message in game.receive():
            await websocket.send_json(
                {"type": "stdout", "data": base64.b64encode(message).decode()}
            )
        await websocket.send_json({"type": "bye"})
        await websocket.close()

    async def add_game_file(
        self,
        content: bytes = fastapi.Body(...),
        filename: str = fastapi.Body(...),
        team_id: str | None = fastapi.Body(None),
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Add a game file.

        Args:
            content: The file content.
            filename: The file name.
            team_id: The team ID of the game owner.
            owner: The user ID of the session owner.
        """
        if team_id is not None and owner not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Cannot specify game ID."
            )
        if self.enable_competition and owner not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition mode is disabled."
            )

        if team_id is None:
            team = await self.teams.team_of(owner)
            if team is None:
                raise fastapi.HTTPException(
                    status_code=400, detail="You are not in a team."
                )
            team_id = team.id

        try:
            self.launcher.add_game_file(
                team_id,
                content,
                filename,
            )
        except GamebattleError as e:
            raise fastapi.HTTPException(status_code=400, detail=e.message)

    async def remove_game_file(
        self,
        filename: str,
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Remove a game file.

        Args:
            filename: The file name.
            owner: The user ID of the session owner.
        """
        if self.enable_competition and owner not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition mode is disabled."
            )
        team = await self.teams.team_of(owner)
        if team is None:
            raise fastapi.HTTPException(
                status_code=400, detail="You are not in a team."
            )
        try:
            self.launcher.remove_game_file(team.id, filename)
        except GamebattleError as e:
            raise fastapi.HTTPException(status_code=400, detail=e.message)

    def admin_remove_game_file(
        self,
        team_id: str,
        filename: str,
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Remove a game file.

        Args:
            game_id: The id of the game to modify (admin only)
            filename: The file name.
            owner: The user ID of the session owner.
        """
        if owner not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Cannot specify game ID."
            )
        try:
            self.launcher.remove_game_file(team_id, filename)
        except GamebattleError as e:
            raise fastapi.HTTPException(status_code=400, detail=e.message)

    async def get_game_files(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> list[File]:
        """List game files.

        Args:
            owner: The user ID of the session owner.
        """
        if self.enable_competition and owner not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition mode is disabled."
            )
        team = await self.teams.team_of(owner)
        if team is None:
            raise fastapi.HTTPException(
                status_code=400, detail="You are not in a team."
            )
        return [
            File(path, content)
            for path, content in self.launcher.get_game_files(team.id).items()
        ]

    def admin_get_game_files(
        self,
        team_id: str,
        owner: str = fastapi.Depends(firebase_email),
    ) -> list[File]:
        """List game files (admin only).

        Args:
            team_id: The id of the team to fetch (admin only)
            owner: The user ID of the session owner.
        """
        if owner not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Cannot specify game ID."
            )
        return [
            File(path, content)
            for path, content in self.launcher.get_game_files(team_id).items()
        ]

    async def get_game_metadata(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> GameMeta | None:
        """Get game metadata.

        Args:
            owner: The user ID of the session owner.
        """
        if self.enable_competition and owner not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition mode is disabled."
            )
        team = await self.teams.team_of(owner)
        if team is None:
            return None
        try:
            return self.launcher[team.id]
        except KeyError:
            return None

    def admin_get_game_metadata(
        self,
        team_id: str,
        owner: str = fastapi.Depends(firebase_email),
    ) -> GameMeta | None:
        """Get game metadata.

        Args:
            team_id: The id of the game to fetch (admin only)
            owner: The user ID of the session owner.
        """
        if owner not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Cannot specify game ID."
            )
        try:
            return self.launcher[team_id]
        except KeyError:
            return None

    async def build_game(
        self,
        name: str = fastapi.Body(...),
        file: str = fastapi.Body(...),
        game_id: str | None = fastapi.Body(None),
        owner: User = fastapi.Depends(firebase_user),
    ) -> None:
        """Build a game.

        Args:
            name: The game name.
            file: The game entrypoint.
            game_id: The id of the game to modify (admin only)
            owner: The user ID of the session owner.
        """
        if game_id is not None and owner.email not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Cannot specify game ID."
            )
        if self.enable_competition and owner.email not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition mode is disabled."
            )

        owner_team = await self.teams.team_of(owner.email)
        owner_team_id = owner_team.id if owner_team else ""

        metadata = GameMeta(
            name,
            owner_team_id if game_id is None else self.launcher[game_id].team_id,
            file,
        )
        await self.launcher.build_game(metadata)

    async def stats(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> Stats:
        """Get the stats of the user.

        Args:
            owner: The user ID of the session owner.
        """
        top = await self.leaderboard()
        team = await self.teams.team_of(owner)
        if team is None:
            return Stats(
                permitted=False,
                started=self.enable_competition,
                elo=None,
                max_elo=top[0].score if top else 1,
                place=None,
                places=len(top) or 1,
                accumulation=0,
                required_accumulation=5,
                reports=0,
                times_played=0,
                game_name=None,
            )
        score, n_played = await self.rating_system.score_and_played(team.id)
        reports = await self.rating_system.fetch_reports(team.id)
        try:
            game_name = self.launcher[team.id].name
        except KeyError:
            game_name = None
        return Stats(
            permitted=True,
            started=self.enable_competition,
            elo=score,
            max_elo=top[0].score if top else 1,
            place=next(
                (i + 1 for i, rating in enumerate(top) if score >= rating.score),
                None,
            ),
            places=len(top) or 1,
            accumulation=await self.preference_store.accumulation_of_preferences_by(
                owner
            ),
            required_accumulation=5,
            reports=len(reports),
            times_played=n_played,
            game_name=game_name,
        )

    async def admin_stats(
        self,
        team_id: str,
        owner: str = fastapi.Depends(firebase_email),
    ) -> list[tuple[str, Stats]]:
        if owner not in self.admin_emails:
            raise fastapi.HTTPException(
                status_code=400, detail="Cannot specify game ID."
            )
        top = await self.leaderboard()
        score, n_played = await self.rating_system.score_and_played_if_exists(team_id)
        reports = await self.rating_system.fetch_reports(team_id)
        try:
            game_name = self.launcher[team_id].name
        except KeyError:
            game_name = None
        return [
            (
                player,
                Stats(
                    permitted=True,
                    started=self.enable_competition,
                    elo=score,
                    max_elo=top[0].score if top else 1,
                    place=(
                        next(
                            (
                                i + 1
                                for i, rating in enumerate(top)
                                if score >= rating.score
                            ),
                            None,
                        )
                        if score
                        else len(top)
                    ),
                    places=len(top) or 1,
                    accumulation=await self.preference_store.accumulation_of_preferences_by(
                        player
                    ),
                    required_accumulation=5,
                    reports=len(reports),
                    times_played=n_played,
                    game_name=game_name,
                ),
            )
            for player in self.teams[team_id].member_emails
        ]

    async def admin_allstats(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> list[tuple[str, GameMeta, Stats]]:
        if owner not in self.admin_emails:
            raise fastapi.HTTPException(status_code=400, detail="Cannot get all stats.")
        return sorted(
            [
                (player, game_meta, stats)
                for game_meta in self.launcher.games
                for player, stats in await self.admin_stats(game_meta.team_id, owner)
            ],
            key=lambda x: x[0],
        )

    async def admin_allstats_csv(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ):
        if owner not in self.admin_emails:
            raise fastapi.HTTPException(status_code=400, detail="Cannot get all stats.")
        fieldnames = [
            "Email",
            "Team ID",
            "Game name",
            "Elo",
            "Place",
            "Times game was played",
            "Comparisons made",
            "Reports",
        ]
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for email, game_meta, stats in await self.admin_allstats(owner):
            writer.writerow(
                {
                    "Email": email,
                    "Team ID": game_meta.team_id,
                    "Game name": game_meta.name,
                    "Elo": stats.elo,
                    "Place": stats.place,
                    "Times game was played": stats.times_played,
                    "Comparisons made": stats.accumulation,
                    "Reports": stats.reports,
                }
            )
        return fastapi.Response(
            content=output.getvalue(),
            media_type="text/csv",
        )

    async def get_game_summary(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> str:
        """Get a one-line AI-generated summary of the game.

        Args:
            owner: The user ID of the session owner.
        """
        team = await self.teams.team_of(owner)
        if team is None:
            return "Ask the admins to make sure you are in a team. Are you using the right email?"
        return await self.launcher.get_game_summary(team.id)

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
                            "color": (
                                (0xFF0000 if report.reason else 0xFFFF00)
                                if accumulated_reports > 3
                                else 0x00FF00
                            ),
                            "fields": [
                                {
                                    "name": "Game",
                                    "value": game.name,
                                    "inline": True,
                                },
                                {
                                    "name": "Author",
                                    "value": game.team_id,
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
        capture_output: bool = fastapi.Body(embed=True),
        restart_game: bool = fastapi.Body(False, embed=True),
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Report a game.

        Args:
            session_id: The session ID.
            game_id: The game ID.
            reason: The reason of the report.
            capture_output: Whether to capture the output.
            restart_game: Whether to restart the game.
            owner: The user ID of the session owner.
        """
        if not self.enable_competition:
            raise fastapi.HTTPException(
                status_code=400, detail="Competition is not enabled."
            )
        try:
            tasks: list[Coroutine] = []
            if restart_game:
                rating = await self.preference_store.get(session_id)
                if rating is not None:
                    return
                session = self.manager.get_session(owner, session_id)
                tasks.append(
                    session.replace_game(
                        game_id,
                        owner,
                        self.launcher,
                        self.rating_system.launch,
                        self.manager.config.container_memory_limit,
                        self.manager.config.container_cpu_limit,
                    )
                )
            game = self.manager.get_game(owner, session_id, game_id)

            output: str | None = None

            if capture_output:
                output = base64.b64encode(game.accumulated_output).decode()

            report = Report(session_id, short_reason, reason, output, owner)
            accumulated_reports = await self.rating_system.report(game.metadata, report)
            if accumulated_reports:
                tasks.append(
                    self._send_report(
                        game.metadata,
                        report,
                        accumulated_reports,
                        game.metadata.team_id,
                    )
                )
            await asyncio.gather(*tasks)
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

    async def exclude_game(
        self,
        team_id: str,
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Exclude a game from competition.

        Args:
            team_id: The team ID of the game to exclude.
            owner: The user ID of the session owner.
        """
        if owner not in self.admin_emails:
            raise fastapi.HTTPException(status_code=403, detail="You are not an admin.")
        await self.rating_system.reports.exclude(team_id)

    async def include_game(
        self,
        team_id: str,
        owner: str = fastapi.Depends(firebase_email),
    ) -> None:
        """Re-include a game in competition.

        Args:
            team_id: The team ID of the game to include.
            owner: The user ID of the session owner.
        """
        if owner not in self.admin_emails:
            raise fastapi.HTTPException(status_code=403, detail="You are not an admin.")
        await self.rating_system.reports.include(team_id)

    async def excluded_games(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> set[str]:
        """List all excluded games.

        Args:
            owner: The user ID of the session owner.
        """
        if owner not in self.admin_emails:
            raise fastapi.HTTPException(status_code=403, detail="You are not an admin.")
        return await self.rating_system.reports.excluded_games()

    async def admin_preference_history(
        self,
        owner: str = fastapi.Depends(firebase_email),
    ) -> list[PreferenceHistoryEntry]:
        """Get the full preference history with ELO changes.

        Args:
            owner: The user ID of the session owner.
        """
        if owner not in self.admin_emails:
            raise fastapi.HTTPException(status_code=403, detail="You are not an admin.")
        preferences = await self.preference_store.sorted_preferences()
        history = self.rating_system.replay_with_history(preferences)
        return [
            PreferenceHistoryEntry(
                games=preference.games,
                first_score=preference.first_score,
                author=preference.author,
                timestamp=preference.timestamp,
                elo_changes=[
                    EloChange(team_id=team_id, before=before, after=after)
                    for team_id, (before, after) in elo_changes.items()
                ],
            )
            for preference, elo_changes in history
        ]

    async def leaderboard(
        self,
    ) -> list[Rating]:
        """Get the leaderboard."""
        top_games: list[Rating] = []
        async for game in self.rating_system.top(self.launcher):
            top_games.append(game)
        return top_games

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
        api.on_event("shutdown")(self.shutdown)
        api.get("/sessions")(self.sessions)
        api.post("/sessions")(self.create_session)
        api.post("/sessions/own")(self.create_own_session)
        api.get("/sessions/{session_id}")(self.session)
        api.delete("/sessions/{session_id}")(self.stop_session)
        api.websocket("/sessions/{session_id}/{game_id}/ws")(self.ws)
        api.post("/sessions/{session_id}/{game_id}/restart")(self.restart_game)
        api.post("/sessions/{session_id}/{game_id}/report")(self.report_game)
        api.get("/reports/{game_id}")(self.fetch_reports)
        api.post("/admin/games/{team_id}/exclude")(self.exclude_game)
        api.delete("/admin/games/{team_id}/exclude")(self.include_game)
        api.get("/admin/games/excluded")(self.excluded_games)
        api.get("/admin/preferences/history")(self.admin_preference_history)
        api.get("/sessions/{session_id}/preference")(self.get_preference)
        api.post("/sessions/{session_id}/preference")(self.set_preference)
        api.get("/leaderboard")(self.leaderboard)
        api.get("/game")(self.get_game_files)
        api.get("/admin/game/{team_id}")(self.admin_get_game_files)
        api.post("/game")(self.add_game_file)
        api.delete("/game/{filename:path}")(self.remove_game_file)
        api.delete("/admin/game/{team_id}/{filename:path}")(self.admin_remove_game_file)
        api.get("/game/meta")(self.get_game_metadata)
        api.get("/admin/game/{team_id}/meta")(self.admin_get_game_metadata)
        api.post("/game/build")(self.build_game)
        api.get("/stats")(self.stats)
        api.get("/stats/{team_id}")(self.admin_stats)
        api.get("/allstats")(self.admin_allstats)
        api.get("/allstats/csv")(self.admin_allstats_csv)
        api.get("/summary")(self.get_game_summary)
        return api

    async def setup(self):
        """Setup the API server."""
        await self.teams.from_yaml(self.teams_path)
        await self.launcher.start()
        if not self.enable_competition:
            await self.launcher.start_generating_summaries()
        await self.preference_store.bind(self.rating_system)

    async def shutdown(self):
        """Shutdown the API server."""
        for session in self.manager.sessions.values():
            await session.stop()


def launch_app() -> fastapi.FastAPI:
    r = redis.Redis(
        host=os.environ.get("REDIS_HOST") or "localhost",
        port=int(os.environ.get("REDIS_PORT") or 6379),
        db=int(os.environ.get("REDIS_DB") or 0),
        password=os.environ.get("REDIS_PASSWORD") or None,
    )
    return GamebattleApi(
        os.environ["GAMES_PATH"],
        os.environ["TEAMS_PATH"],
        os.environ.get("AUTOBUILD_TEAMS") == "true",
        RedisPreferenceStore(r),
        EloRatingSystem(RedisReportStore(r)),
        os.environ.get("ENABLE_COMPETITION") == "true",
        os.environ.get("REPORT_WEBHOOK") or None,
        json.loads(os.environ.get("ADMIN_EMAILS") or "[]"),
    )()
