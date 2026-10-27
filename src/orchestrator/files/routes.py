"""Game files over HTTP.

A player reads and writes only their own latest snapshot, at ``/files``. Admins
reach any game through ``/games/{game_id}/...`` and any snapshot by id.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from orchestrator.access import EditRequired
from orchestrator.auth import AdminRequired
from orchestrator.dependencies import NotifierDep, PlayerDep, StoreDep
from orchestrator.files.models import (
    DirtySnapshot,
    GameFile,
    GameFiles,
    RevisionConflict,
    Snapshot,
    TooManyFiles,
    valid_path,
)
from orchestrator.files.mongo import MongoGameFiles
from orchestrator.store import Missing, Store

logger = logging.getLogger("orchestrator")
router = APIRouter()

MAX_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class SetFile:
    expected_current: UUID
    file: GameFile


@dataclass(frozen=True)
class Publish:
    snapshot_id: UUID


@router.get("/files")
async def read_own(player: PlayerDep) -> Snapshot:
    return await player.files.get_snapshot("current")


@router.post("/files")
async def set_own_file(body: SetFile, player: PlayerDep, _: EditRequired) -> Snapshot:
    return await _set_file(player.files, body, player.id)


@router.get("/games/{game_id}/files")
async def read_latest(game_id: UUID, store: StoreDep, _admin: AdminRequired) -> Snapshot:
    files = await _game_files(store, game_id)
    return await files.get_snapshot("current")


@router.get("/games/{game_id}/files/history")
async def history(
    game_id: UUID,
    store: StoreDep,
    _admin: AdminRequired,
    before: UUID | None = None,
    limit: int = 100,
) -> list[Snapshot]:
    files = await _game_files(store, game_id)
    try:
        return await files.get_snapshot_history(before=before, limit=limit)
    except KeyError as error:
        raise HTTPException(404, "snapshot not found") from error


@router.post("/games/{game_id}/files")
async def set_file(
    game_id: UUID,
    body: SetFile,
    store: StoreDep,
    actor: PlayerDep,
    _admin: AdminRequired,
) -> Snapshot:
    return await _set_file(await _game_files(store, game_id), body, actor.id)


@router.get("/games/{game_id}/publish")
async def read_published(
    game_id: UUID, store: StoreDep, _admin: AdminRequired
) -> Snapshot:
    files = await _game_files(store, game_id)
    try:
        return await files.get_snapshot("published")
    except KeyError as error:
        missing = error.args[0] if error.args else None
        if missing == "published":
            raise HTTPException(404, "not published") from error
        raise HTTPException(404, "snapshot not found") from error


@router.post("/games/{game_id}/publish")
async def publish(
    game_id: UUID, body: Publish, store: StoreDep, _admin: AdminRequired
) -> Snapshot:
    files = await _game_files(store, game_id)
    try:
        await files.publish(body.snapshot_id)
        return await files.get_snapshot("published")
    except KeyError as error:
        raise HTTPException(404, "snapshot not found") from error
    except DirtySnapshot as error:
        raise HTTPException(400, "snapshot is not clean") from error


@router.delete("/games/{game_id}/publish", status_code=204)
async def unpublish(
    game_id: UUID, store: StoreDep, notifier: NotifierDep, _admin: AdminRequired
) -> Response:
    files = await _game_files(store, game_id)
    was_published = await MongoGameFiles(store.db, game_id).published_revision() is not None
    await files.unpublish()
    if was_published:
        try:
            await notifier.game_removed(game_id, "admin", 0)
        except Exception:
            logger.exception("could not notify webhooks")
    return Response(status_code=204)


async def _game_files(store: Store, game_id: UUID) -> GameFiles:
    try:
        return await store.game_files(game_id)
    except Missing as error:
        raise HTTPException(404, "game not found") from error


async def _set_file(files: GameFiles, body: SetFile, author: UUID) -> Snapshot:
    check_file(body.file)
    try:
        return await files.set_file(
            body.file, expected_current=body.expected_current, author=author
        )
    except TooManyFiles as error:
        raise HTTPException(400, "too many files") from error
    except RevisionConflict as error:
        raise HTTPException(
            409, "expected_current is not the current snapshot"
        ) from error
    except KeyError as error:
        raise HTTPException(404, "snapshot not found") from error


def check_file(file: GameFile) -> None:
    check_path(file.path)
    contents = [file.saved_content]
    if file.draft is not None:
        contents.append(file.draft.content)
    if any(content is not None and len(content) > MAX_FILE_BYTES for content in contents):
        raise HTTPException(400, "file is too large")


def check_path(path: str) -> None:
    if not valid_path(path):
        raise HTTPException(400, "invalid file name")
