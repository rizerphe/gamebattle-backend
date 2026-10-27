"""Game files in MongoDB.

Each snapshot is one immutable document in ``snapshots``. Its ``number`` is its
position in the player's linear history, and ``(player, number)`` is unique, so
a snapshot can only be appended on top of the current one: a stale parent's
successor number is already taken. ``players`` holds the submitted and
published pointers.
"""

import time
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from orchestrator.files.models import (
    MAX_FILES,
    DirtySnapshot,
    GameFile,
    GameFiles,
    RevisionConflict,
    Snapshot,
    SnapshotReason,
    TooManyFiles,
)

_snapshot = TypeAdapter(Snapshot)


async def create_indexes(db: AsyncDatabase) -> None:
    await db.snapshots.create_index(
        [("player", ASCENDING), ("number", ASCENDING)], unique=True
    )


class MongoGameFiles(GameFiles):
    def __init__(self, db: AsyncDatabase, player: UUID) -> None:
        self._snapshots = db.snapshots
        self._players = db.players
        self._player = player

    async def get_snapshot(
        self, reference: UUID | Literal["current", "submitted", "published"]
    ) -> Snapshot:
        if reference == "current":
            return _load(await self._current())
        if reference in ("submitted", "published"):
            player = await self._players.find_one({"_id": self._player})
            pointer = player.get(reference) if player else None
            if pointer is None:
                raise KeyError(reference)
            reference = pointer
        return _load(await self._find(reference))

    async def get_snapshot_history(
        self, *, before: UUID | None = None, limit: int | None = 100
    ) -> list[Snapshot]:
        await self._current()
        query: dict[str, Any] = {"player": self._player}
        if before is not None:
            query["number"] = {"$lt": (await self._find(before))["number"]}
        cursor = self._snapshots.find(query).sort("number", DESCENDING)
        if limit is not None:
            cursor = cursor.limit(limit)
        return [_load(doc) for doc in reversed(await cursor.to_list())]

    async def submit(self, files: tuple[GameFile, ...], *, author: UUID) -> Snapshot:
        if len(files) > MAX_FILES:
            raise TooManyFiles(len(files))
        if any(file.modified for file in files):
            raise DirtySnapshot("import")
        while True:
            try:
                snapshot = await self._append(
                    await self._current(), files, SnapshotReason.IMPORT, author
                )
                break
            except RevisionConflict:
                continue
        await self._players.update_one(
            {"_id": self._player},
            {
                "$set": {
                    "submitted": snapshot.id,
                    "published": snapshot.id,
                    "published_at": time.time(),
                }
            },
            upsert=True,
        )
        return snapshot

    async def set_file(
        self, new_file: GameFile, *, expected_current: UUID, author: UUID
    ) -> Snapshot:
        try:
            parent = await self._find(expected_current)
        except KeyError as error:
            raise RevisionConflict(expected_current) from error
        current = _load(parent)
        existing = next(
            (file for file in current.files if file.path == new_file.path), None
        )
        kept = tuple(file for file in current.files if file.path != new_file.path)

        new_file = _drop_unchanged_draft(new_file)

        if new_file.saved_content is None and new_file.draft is None:
            if existing is None:
                return await self._if_still_current(parent, current, expected_current)
            return await self._append(parent, kept, SnapshotReason.DELETE, author)

        if existing == new_file:
            return await self._if_still_current(parent, current, expected_current)

        if existing is None and len(current.files) >= MAX_FILES:
            raise TooManyFiles(len(current.files))

        if (
            existing is not None
            and existing.draft is not None
            and new_file.draft is None
            and new_file.saved_content == existing.saved_content
        ):
            reason = SnapshotReason.DISCARD
        elif new_file.draft is not None:
            reason = SnapshotReason.MODIFY
        elif existing is None:
            reason = SnapshotReason.UPLOAD
        else:
            reason = SnapshotReason.SAVE

        files = tuple(sorted((*kept, new_file), key=lambda file: file.path))
        return await self._append(parent, files, reason, author)

    async def publish(self, snapshot_id: UUID) -> None:
        snapshot = _load(await self._find(snapshot_id))
        if snapshot.modified:
            raise DirtySnapshot(snapshot_id)
        await self._players.update_one(
            {"_id": self._player},
            {"$set": {"published": snapshot_id, "published_at": time.time()}},
            upsert=True,
        )

    async def unpublish(self) -> None:
        await self._players.update_one(
            {"_id": self._player}, {"$unset": {"published": "", "published_at": ""}}
        )

    async def published_revision(self) -> tuple[UUID, float] | None:
        player = await self._players.find_one(
            {"_id": self._player}, {"published": 1, "published_at": 1}
        )
        if player is None:
            return None
        published = player.get("published")
        if published is None:
            return None
        published_at = player.get("published_at")
        if not isinstance(published_at, (int, float)):
            published_at = 0.0
        return published, float(published_at)

    async def _if_still_current(
        self, parent: dict[str, Any], current: Snapshot, expected_current: UUID
    ) -> Snapshot:
        if await self._snapshots.find_one(
            {"player": self._player, "number": parent["number"] + 1}
        ):
            raise RevisionConflict(expected_current)
        return current

    async def _current(self) -> dict[str, Any]:
        latest = await self._snapshots.find_one(
            {"player": self._player}, sort=[("number", DESCENDING)]
        )
        if latest is not None:
            return latest
        initial = Snapshot(
            id=uuid4(),
            parent_id=None,
            timestamp=time.time(),
            author=self._player,
            reason=SnapshotReason.INITIAL,
            files=(),
        )
        try:
            await self._snapshots.insert_one(
                _dump(initial, player=self._player, number=0)
            )
        except DuplicateKeyError:
            pass
        return await self._current()

    async def _find(self, snapshot_id: UUID) -> dict[str, Any]:
        found = await self._snapshots.find_one(
            {"_id": snapshot_id, "player": self._player}
        )
        if found is None:
            raise KeyError(snapshot_id)
        return found

    async def _append(
        self,
        parent: dict[str, Any],
        files: tuple[GameFile, ...],
        reason: SnapshotReason,
        author: UUID,
    ) -> Snapshot:
        snapshot = Snapshot(
            id=uuid4(),
            parent_id=parent["_id"],
            timestamp=time.time(),
            author=author,
            reason=reason,
            files=files,
        )
        try:
            await self._snapshots.insert_one(
                _dump(snapshot, player=self._player, number=parent["number"] + 1)
            )
        except DuplicateKeyError as error:
            raise RevisionConflict(parent["_id"]) from error
        return snapshot


def _drop_unchanged_draft(file: GameFile) -> GameFile:
    """A draft that matches the saved bytes is not a change."""

    if file.draft is not None and file.draft.content == file.saved_content:
        return GameFile(path=file.path, saved_content=file.saved_content, draft=None)
    return file


def _dump(snapshot: Snapshot, **extra: Any) -> dict[str, Any]:
    doc = _snapshot.dump_python(snapshot, exclude={"modified"})
    doc["_id"] = doc.pop("id")
    return doc | extra


def _load(doc: dict[str, Any]) -> Snapshot:
    data = {**doc, "id": doc["_id"]}
    # Revisions stored before authors existed were written by the game's owner.
    if data.get("author") is None:
        data["author"] = doc["player"]
    return _snapshot.validate_python(data)
