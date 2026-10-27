"""Webhooks over HTTP. Admins only."""

from dataclasses import dataclass, field
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from orchestrator.auth import AdminRequired
from orchestrator.dependencies import NotifierDep, StoreDep
from orchestrator.reports.mongo import REPORTS_BEFORE_UNPUBLISH
from orchestrator.webhooks.models import Webhook
from orchestrator.webhooks.mongo import MongoWebhooks

router = APIRouter()

_MAX_URL_LENGTH = 2000


@dataclass(frozen=True)
class WebhookBody:
    url: str
    thresholds: list[int] = field(default_factory=list)
    on_removed: bool = False


@router.get("/webhooks")
async def list_webhooks(store: StoreDep, _admin: AdminRequired) -> list[Webhook]:
    return await MongoWebhooks(store.db).all()


@router.post("/webhooks")
async def create_webhook(body: WebhookBody, store: StoreDep, _admin: AdminRequired) -> Webhook:
    url, thresholds = _checked(body)
    return await MongoWebhooks(store.db).create(url, thresholds, body.on_removed)


@router.put("/webhooks/{webhook_id}")
async def replace_webhook(
    webhook_id: UUID, body: WebhookBody, store: StoreDep, _admin: AdminRequired
) -> Webhook:
    url, thresholds = _checked(body)
    hook = await MongoWebhooks(store.db).replace(webhook_id, url, thresholds, body.on_removed)
    if hook is None:
        raise HTTPException(404, "webhook not found")
    return hook


@router.delete("/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: UUID, store: StoreDep, _admin: AdminRequired) -> Response:
    if not await MongoWebhooks(store.db).delete(webhook_id):
        raise HTTPException(404, "webhook not found")
    return Response(status_code=204)


@router.post("/webhooks/{webhook_id}/test", status_code=204)
async def test_webhook(
    webhook_id: UUID, store: StoreDep, notifier: NotifierDep, _admin: AdminRequired
) -> Response:
    hook = await MongoWebhooks(store.db).get(webhook_id)
    if hook is None:
        raise HTTPException(404, "webhook not found")
    try:
        await notifier.test(hook)
    except httpx.HTTPStatusError as error:
        raise HTTPException(502, f"webhook answered {error.response.status_code}") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "webhook could not be reached") from error
    return Response(status_code=204)


def _checked(body: WebhookBody) -> tuple[str, tuple[int, ...]]:
    url = body.url.strip()
    parsed = urlparse(url)
    if len(url) > _MAX_URL_LENGTH or parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(400, "invalid webhook url")
    if any(not 1 <= count <= REPORTS_BEFORE_UNPUBLISH for count in body.thresholds):
        raise HTTPException(400, "invalid report threshold")
    thresholds = tuple(sorted(set(body.thresholds)))
    if not thresholds and not body.on_removed:
        raise HTTPException(400, "webhook needs a threshold or removal notifications")
    return url, thresholds
