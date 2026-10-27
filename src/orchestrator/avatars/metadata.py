"""Firebase photo URLs stored on player documents."""

from datetime import UTC, datetime
from uuid import UUID

from orchestrator.store import Store


async def record_firebase_photo(
    store: Store, player_id: UUID, source_url: str | None
) -> None:
    document = await store.db.players.find_one(
        {"_id": player_id}, {"firebase_photo_url": 1}
    )
    previous = document.get("firebase_photo_url") if document else None
    if previous == source_url:
        return
    await store.db.players.update_one(
        {"_id": player_id},
        {
            "$set": {"firebase_photo_url": source_url},
            "$unset": {"firebase_photo_cached_at": ""},
        },
    )


def cache_fresh(cached_at: object, ttl_seconds: float) -> bool:
    if not isinstance(cached_at, datetime):
        return False
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - cached_at).total_seconds() < ttl_seconds
