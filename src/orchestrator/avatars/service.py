"""Download and cache Firebase profile photos."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx

from orchestrator.avatars.metadata import cache_fresh, record_firebase_photo
from orchestrator.avatars.storage import AvatarStore, avatar_object_key
from orchestrator.store import Store
from orchestrator.submissions.firebase import UserDirectory


class AvatarNotFound(Exception):
    """No linked account or no photo for this player."""


async def fetch_avatar(
    store: Store,
    avatars: AvatarStore,
    users: UserDirectory,
    http: httpx.AsyncClient,
    student_id: UUID,
    *,
    cache_ttl: timedelta,
) -> tuple[bytes, str]:
    document = await store.db.players.find_one(
        {"_id": student_id},
        {"firebase_uid": 1, "firebase_photo_url": 1, "firebase_photo_cached_at": 1},
    )
    if not document:
        raise AvatarNotFound
    uid = document.get("firebase_uid")
    if not isinstance(uid, str) or not uid:
        raise AvatarNotFound

    source_url = document.get("firebase_photo_url")
    if not isinstance(source_url, str) or not source_url:
        account = await users.profile(uid)
        if account is None or not account.photo:
            raise AvatarNotFound
        source_url = account.photo
        await record_firebase_photo(store, student_id, source_url)

    key = avatar_object_key(uid)
    ttl_seconds = cache_ttl.total_seconds()
    if cache_fresh(document.get("firebase_photo_cached_at"), ttl_seconds):
        stored = await avatars.get(key)
        if stored is not None:
            return stored.body, stored.content_type

    body, content_type = await _download(http, source_url)
    await avatars.put(key, body, content_type)
    await store.db.players.update_one(
        {"_id": student_id},
        {"$set": {"firebase_photo_cached_at": datetime.now(UTC)}},
    )
    return body, content_type


async def _download(http: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    try:
        response = await http.get(
            url,
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "gamebattle-orchestrator/1.0"},
        )
    except httpx.HTTPError as error:
        raise AvatarNotFound from error
    if response.status_code != 200:
        raise AvatarNotFound
    body = response.content
    if not body:
        raise AvatarNotFound
    content_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"
    return body, content_type
