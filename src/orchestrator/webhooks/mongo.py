"""Webhooks in MongoDB: one document per webhook in ``webhooks``."""

import time
from typing import Any
from uuid import UUID, uuid4

from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

from orchestrator.webhooks.models import Event, Webhook


class MongoWebhooks:
    def __init__(self, db: AsyncDatabase) -> None:
        self._webhooks = db.webhooks

    async def all(self) -> list[Webhook]:
        docs = await self._webhooks.find().sort("created_at", ASCENDING).to_list()
        return [_load(doc) for doc in docs]

    async def get(self, webhook_id: UUID) -> Webhook | None:
        doc = await self._webhooks.find_one({"_id": webhook_id})
        return _load(doc) if doc else None

    async def create(
        self, url: str, event: Event, threshold: int | None
    ) -> Webhook:
        doc = {
            "_id": uuid4(),
            "url": url,
            "event": event,
            "threshold": threshold,
            "created_at": time.time(),
        }
        await self._webhooks.insert_one(doc)
        return _load(doc)

    async def replace(
        self, webhook_id: UUID, url: str, event: Event, threshold: int | None
    ) -> Webhook | None:
        doc = await self._webhooks.find_one_and_update(
            {"_id": webhook_id},
            {"$set": {"url": url, "event": event, "threshold": threshold}},
            return_document=True,
        )
        return _load(doc) if doc else None

    async def delete(self, webhook_id: UUID) -> bool:
        result = await self._webhooks.delete_one({"_id": webhook_id})
        return result.deleted_count == 1


def _load(doc: dict[str, Any]) -> Webhook:
    return Webhook(
        id=doc["_id"],
        url=doc["url"],
        event=doc["event"],
        threshold=doc.get("threshold"),
        created_at=doc["created_at"],
    )
