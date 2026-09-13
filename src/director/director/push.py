"""Web push for approvals (phase 11 S8): a phone learns that something needs a decision.

A browser subscribes once (``POST /api/push/subscriptions``) and the director
keeps the endpoint. ``PushRelay`` listens to the realtime stream and, on
every ``approval_created``, sends every subscription a small notification:
the approval number, its summary and the link into the inbox. Never an
email body, never a name beyond what the summary already says. A gone
subscription (404/410 from the push service) is dropped.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from director.api.approvals import ApprovalsGateway
from sc_core.app.realtime import Realtime, RealtimeEvent
from sc_core.infra.db import Database
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import NotFound, ScError


class PushSubscription(StrictModel):
    id: int | None = None
    endpoint: str
    p256dh: str
    auth: str
    user_email: str | None = None
    created_at: datetime | None = None

    def to_web_push(self) -> dict[str, Any]:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


@runtime_checkable
class PushStore(Protocol):
    async def add(self, subscription: PushSubscription) -> PushSubscription: ...

    async def remove(self, endpoint: str) -> bool: ...

    async def all(self) -> list[PushSubscription]: ...


def _subscription(row: dict[str, Any]) -> PushSubscription:
    return PushSubscription(
        id=int(row["id"]),
        endpoint=str(row["endpoint"]),
        p256dh=str(row["p256dh"]),
        auth=str(row["auth"]),
        user_email=row.get("user_email"),
        created_at=row.get("created_at"),
    )


class PostgresPushStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, subscription: PushSubscription) -> PushSubscription:
        row = await self._db.fetch_one(
            "INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_email) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (endpoint) DO UPDATE SET "
            "p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth, user_email = EXCLUDED.user_email "
            "RETURNING *",
            (
                subscription.endpoint,
                subscription.p256dh,
                subscription.auth,
                subscription.user_email,
            ),
        )
        assert row is not None
        return _subscription(row)

    async def remove(self, endpoint: str) -> bool:
        deleted = await self._db.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,)
        )
        return bool(deleted)

    async def all(self) -> list[PushSubscription]:
        rows = await self._db.fetch_all("SELECT * FROM push_subscriptions ORDER BY id")
        return [_subscription(r) for r in rows]


class MemoryPushStore:
    def __init__(self) -> None:
        self.rows: dict[str, PushSubscription] = {}

    async def add(self, subscription: PushSubscription) -> PushSubscription:
        saved = subscription.model_copy(update={"id": len(self.rows) + 1})
        self.rows[subscription.endpoint] = saved
        return saved

    async def remove(self, endpoint: str) -> bool:
        return self.rows.pop(endpoint, None) is not None

    async def all(self) -> list[PushSubscription]:
        return list(self.rows.values())


# --- sending -------------------------------------------------------------------------------


@runtime_checkable
class PushSender(Protocol):
    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> bool:
        """True when delivered (or accepted); False when the subscription is gone."""
        ...


class WebPushSender:
    """VAPID-signed web push through ``pywebpush`` (synchronous, so run in a thread)."""

    def __init__(self, *, private_key: str, subject: str) -> None:
        self._private_key = private_key
        self._subject = subject

    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> bool:
        from pywebpush import WebPushException, webpush

        def post() -> bool:
            try:
                webpush(
                    subscription_info=subscription.to_web_push(),
                    data=json.dumps(payload),
                    vapid_private_key=self._private_key,
                    vapid_claims={"sub": self._subject},
                    ttl=3600,
                )
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (404, 410):
                    return False
                logger.warning("web push failed ({}): {}", status, exc)
            return True

        return await asyncio.to_thread(post)


class MemoryPushSender:
    def __init__(self, *, gone: set[str] | None = None) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.gone = gone or set()

    async def send(self, subscription: PushSubscription, payload: dict[str, Any]) -> bool:
        if subscription.endpoint in self.gone:
            return False
        self.sent.append((subscription.endpoint, payload))
        return True


class PushRelay:
    """Turns ``approval_created`` stream events into notifications for every subscriber."""

    def __init__(
        self,
        *,
        realtime: Realtime,
        store: PushStore,
        sender: PushSender,
        approvals: ApprovalsGateway,
        control_tower_url: str,
    ) -> None:
        self._realtime = realtime
        self._store = store
        self._sender = sender
        self._approvals = approvals
        self._ui = control_tower_url.rstrip("/")

    async def run(self) -> None:
        async for event in self._realtime.subscribe():
            try:
                await self.handle(event)
            except Exception as exc:  # noqa: BLE001 - one bad event must not stop the relay
                logger.warning("push relay skipped an event: {}", exc)

    async def handle(self, event: RealtimeEvent) -> int:
        """Notify subscribers of a new approval; returns how many notifications went out."""
        if event.kind != "approval_created":
            return 0
        approval_id = event.payload.get("approval_id")
        if not approval_id:
            return 0
        subscriptions = await self._store.all()
        if not subscriptions:
            return 0
        try:
            approval = await self._approvals.get(int(approval_id))
        except (NotFound, ScError) as exc:
            logger.warning("push relay could not read approval {}: {}", approval_id, exc)
            return 0
        payload = {
            "title": f"Approval #{approval.id}",
            "body": approval.summary[:140],
            "url": f"{self._ui}/approvals?id={approval.id}",
            "tag": f"approval-{approval.id}",
        }
        sent = 0
        for subscription in subscriptions:
            if await self._sender.send(subscription, payload):
                sent += 1
            else:
                await self._store.remove(subscription.endpoint)
                logger.bind(endpoint=subscription.endpoint[:40]).info("push subscription gone")
        logger.bind(approval_id=approval.id, sent=sent).info("approval pushed")
        return sent


__all__ = [
    "MemoryPushSender",
    "MemoryPushStore",
    "PostgresPushStore",
    "PushRelay",
    "PushSender",
    "PushStore",
    "PushSubscription",
    "WebPushSender",
]
