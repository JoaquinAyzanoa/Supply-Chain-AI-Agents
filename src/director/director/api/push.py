"""Push subscriptions for the installable Control Tower (phase 11 S8)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field

from director.api.auth import Principal, Viewer
from director.push import PushStore, PushSubscription
from sc_core.infra.settings import Settings
from sc_core.schema.base import StrictModel

router = APIRouter(prefix="/push", tags=["push"])


class PushKey(StrictModel):
    enabled: bool
    public_key: str | None = None


class SubscriptionKeys(StrictModel):
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class SubscribeRequest(StrictModel):
    endpoint: str = Field(min_length=1, max_length=2000)
    keys: SubscriptionKeys


class UnsubscribeRequest(StrictModel):
    endpoint: str = Field(min_length=1, max_length=2000)


@router.get("/key", response_model=PushKey)
async def push_key(_: Principal = Viewer, settings: Settings = Injected(Settings)) -> PushKey:
    public = settings.ui.vapid_public_key
    return PushKey(
        enabled=bool(public and settings.ui.vapid_private_key.get_secret_value()),
        public_key=public or None,
    )


@router.post("/subscriptions", status_code=201, response_model=dict[str, int])
async def subscribe(
    body: SubscribeRequest,
    principal: Principal = Viewer,
    store: PushStore = Injected(PushStore),  # type: ignore[type-abstract]
    settings: Settings = Injected(Settings),
) -> dict[str, int]:
    if not settings.ui.vapid_public_key:
        raise HTTPException(status_code=409, detail="push notifications are not configured")
    saved = await store.add(
        PushSubscription(
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            user_email=principal.email,
        )
    )
    logger.bind(by=principal.email).info("push subscription added")
    return {"id": saved.id or 0}


@router.delete("/subscriptions", status_code=204)
async def unsubscribe(
    body: UnsubscribeRequest,
    principal: Principal = Viewer,
    store: PushStore = Injected(PushStore),  # type: ignore[type-abstract]
) -> None:
    removed = await store.remove(body.endpoint)
    logger.bind(by=principal.email, removed=removed).info("push subscription removed")
