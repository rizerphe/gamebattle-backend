import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pymongo import AsyncMongoClient

from orchestrator.avatars.routes import router as avatars_router
from orchestrator.avatars.storage import open_avatar_store
from orchestrator.admin.routes import router as admin_router
from orchestrator.comparisons.mongo import create_indexes as create_comparison_indexes
from orchestrator.comparisons.routes import router as comparisons_router
from orchestrator.comparisons.service import Comparisons
from orchestrator.files.mongo import create_indexes
from orchestrator.files.routes import router as files_router
from orchestrator.games.routes import router as games_router
from orchestrator.phases.routes import router as phases_router
from orchestrator.phases.sweep import sweep_forever
from orchestrator.players.routes import router as players_router
from orchestrator.reports.routes import router as reports_router
from orchestrator.sessions.mongo import create_indexes as create_session_indexes
from orchestrator.sessions.routes import router as sessions_router
from orchestrator.sessions.service import Sessions
from orchestrator.summary.routes import router as summary_router
from orchestrator.settings import Settings
from orchestrator.stats.routes import router as stats_router
from orchestrator.stats.service import StatsService
from orchestrator.store import Store
from orchestrator.submissions.firebase import FirebaseUsers
from orchestrator.submissions.routes import router as submissions_router
from orchestrator.terminal_client import TerminalClient
from orchestrator.tokens import FirebaseTokenVerifier

logger = logging.getLogger("orchestrator")


def _install_error_handlers(app: FastAPI) -> None:
    """Known failures keep their status and message. Anything else is a vague 500."""

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, error: Exception) -> Response:
        if isinstance(error, HTTPException):
            return await http_exception_handler(request, error)
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)


def create_app() -> FastAPI:
    config = Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with (
            httpx.AsyncClient() as http,
            AsyncMongoClient(config.mongo_url, uuidRepresentation="standard") as mongo,
        ):
            db = mongo[config.mongo_database]
            await create_indexes(db)
            await create_session_indexes(db)
            await create_comparison_indexes(db)
            app.state.settings = config
            app.state.verifier = FirebaseTokenVerifier(config.firebase_project_id)
            app.state.http = http
            store = Store(db)
            app.state.store = store
            avatars = open_avatar_store(config)
            await avatars.ensure_bucket()
            app.state.avatars = avatars
            app.state.sessions = Sessions(
                store,
                TerminalClient(http, config.terminal_service_url, config.service_token),
            )
            app.state.comparisons = Comparisons(store, app.state.sessions)
            app.state.stats = StatsService(db, app.state.comparisons)
            app.state.users = FirebaseUsers(
                config.firebase_credentials, config.firebase_project_id, http
            )
            sweeper = asyncio.create_task(sweep_forever(store, app.state.sessions, app.state.comparisons))
            try:
                yield
            finally:
                sweeper.cancel()

    app = FastAPI(lifespan=lifespan)
    _install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(avatars_router)
    app.include_router(admin_router)
    app.include_router(comparisons_router)
    app.include_router(files_router)
    app.include_router(games_router)
    app.include_router(phases_router)
    app.include_router(players_router)
    app.include_router(reports_router)
    app.include_router(sessions_router)
    app.include_router(stats_router)
    app.include_router(submissions_router)
    app.include_router(summary_router)
    return app


app = create_app()
