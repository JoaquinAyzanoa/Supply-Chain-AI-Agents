"""Signed event delivery with an outbox.

``HmacSigner`` signs and verifies request bodies the same way the Odoo
approval callback does (``X-SC-Signature: sha256=<hex>``), so one secret
covers every internal HTTP hop.

``EventPublisher.publish`` POSTs an event to the director and retries
transient failures with backoff. When delivery still fails, the event is
written to the ``event_outbox`` table and ``flush_outbox`` retries it on the
next run. Events carry identifiers only, so storing them is within the
"no email persisted" rule.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi_injector import Injected
from loguru import logger

from sc_core.infra.db import Database
from sc_core.infra.settings import EventsCfg, Settings
from sc_core.schema.events import BaseEvent
from sc_core.shared.errors import ConfigurationError

SIGNATURE_HEADER = "X-SC-Signature"
EVENT_TYPE_HEADER = "X-SC-Event-Type"
EVENT_ID_HEADER = "X-SC-Event-Id"

Sleep = Callable[[float], Awaitable[None]]


class HmacSigner:
    def __init__(self, secret: str) -> None:
        if not secret:
            raise ConfigurationError("SC__EVENTS__SIGNING_SECRET is required to sign events")
        self._key = secret.encode("utf-8")

    def sign(self, body: bytes) -> str:
        return "sha256=" + hmac.new(self._key, body, hashlib.sha256).hexdigest()

    def verify(self, body: bytes, signature: str | None) -> bool:
        if not signature:
            return False
        return hmac.compare_digest(self.sign(body), signature.strip())


async def require_signature(request: Request, settings: Settings = Injected(Settings)) -> bytes:
    """FastAPI dependency: the raw body once its signature has been verified.

    Without a configured secret every request is rejected; a service that
    forgot the secret must fail visibly rather than accept anything.
    """
    body = await request.body()
    secret = settings.events.signing_secret.get_secret_value()
    if not secret:
        logger.error("SC__EVENTS__SIGNING_SECRET not set; rejecting signed request")
        raise HTTPException(status_code=401, detail="signature verification not configured")
    if not HmacSigner(secret).verify(body, request.headers.get(SIGNATURE_HEADER)):
        raise HTTPException(status_code=401, detail="invalid signature")
    return body


SignedBody = Depends(require_signature)


def encode_event(event: BaseEvent) -> bytes:
    """Canonical JSON bytes: what is signed, sent and stored in the outbox."""
    return event.model_dump_json(exclude_none=False).encode("utf-8")


def signed_headers(signer: HmacSigner, body: bytes, **extra: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: signer.sign(body),
        **extra,
    }


# --- outbox -----------------------------------------------------------------------


class Outbox(Protocol):
    async def add(self, event_id: str, event_type: str, target: str, body: bytes) -> None: ...

    async def pending(self, limit: int = 100) -> list[dict[str, Any]]: ...

    async def delete(self, event_id: str) -> None: ...

    async def record_failure(self, event_id: str, error: str) -> None: ...


class MemoryOutbox:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def add(self, event_id: str, event_type: str, target: str, body: bytes) -> None:
        self.rows.setdefault(
            event_id,
            {
                "event_id": event_id,
                "event_type": event_type,
                "target": target,
                "body": body,
                "attempts": 0,
                "last_error": None,
            },
        )

    async def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in list(self.rows.values())[:limit]]

    async def delete(self, event_id: str) -> None:
        self.rows.pop(event_id, None)

    async def record_failure(self, event_id: str, error: str) -> None:
        row = self.rows.get(event_id)
        if row is not None:
            row["attempts"] += 1
            row["last_error"] = error


class PostgresOutbox:
    """``event_outbox`` table from migration 002."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, event_id: str, event_type: str, target: str, body: bytes) -> None:
        await self._db.execute(
            "INSERT INTO event_outbox (event_id, event_type, target, body) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
            (event_id, event_type, target, body.decode("utf-8")),
        )

    async def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT event_id, event_type, target, body, attempts, last_error "
            "FROM event_outbox ORDER BY created_at LIMIT %s",
            (limit,),
        )
        for row in rows:
            row["body"] = row["body"].encode("utf-8")
        return rows

    async def delete(self, event_id: str) -> None:
        await self._db.execute("DELETE FROM event_outbox WHERE event_id = %s", (event_id,))

    async def record_failure(self, event_id: str, error: str) -> None:
        await self._db.execute(
            "UPDATE event_outbox SET attempts = attempts + 1, last_error = %s, "
            "last_attempt_at = now() WHERE event_id = %s",
            (error[:500], event_id),
        )


# --- publisher --------------------------------------------------------------------


class DeliveryFailed(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class EventPublisher:
    def __init__(
        self,
        cfg: EventsCfg,
        *,
        outbox: Outbox,
        http: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._cfg = cfg
        self._signer = HmacSigner(cfg.signing_secret.get_secret_value())
        self._outbox = outbox
        self._http = http or httpx.AsyncClient(timeout=cfg.timeout_seconds)
        self._sleep = sleep
        self.url = cfg.director_url.rstrip("/") + "/events"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def publish(self, event: BaseEvent) -> bool:
        """Deliver now; on failure park the event in the outbox. Never raises.

        Returns ``True`` when the director accepted it in this call.
        """
        body = encode_event(event)
        try:
            await self._deliver(body, event.type, event.event_id)
            return True
        except DeliveryFailed as exc:
            logger.bind(event_id=event.event_id, event_type=event.type).warning(
                "event not delivered, queued in outbox: {}", exc
            )
            await self._outbox.add(event.event_id, event.type, self.url, body)
            await self._outbox.record_failure(event.event_id, str(exc))
            return False

    async def flush_outbox(self, limit: int = 100) -> int:
        """Retry queued events in order; stop at the first that still fails (keeps order)."""
        delivered = 0
        for row in await self._outbox.pending(limit):
            try:
                await self._deliver(row["body"], row["event_type"], row["event_id"], attempts=1)
            except DeliveryFailed as exc:
                await self._outbox.record_failure(row["event_id"], str(exc))
                break
            await self._outbox.delete(row["event_id"])
            delivered += 1
        return delivered

    async def _deliver(
        self, body: bytes, event_type: str, event_id: str, *, attempts: int | None = None
    ) -> None:
        headers = signed_headers(self._signer, body)
        headers[EVENT_TYPE_HEADER] = event_type
        headers[EVENT_ID_HEADER] = event_id
        max_attempts = attempts or self._cfg.max_attempts
        last: DeliveryFailed | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                await self._post(body, headers)
                return
            except DeliveryFailed as exc:
                last = exc
                if not exc.retryable or attempt == max_attempts:
                    break
                await self._sleep(self._cfg.backoff_seconds * 2 ** (attempt - 1))
        assert last is not None
        raise last

    async def _post(self, body: bytes, headers: dict[str, str]) -> None:
        try:
            response = await self._http.post(self.url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise DeliveryFailed(f"{type(exc).__name__}: {exc}", retryable=True) from exc
        if response.status_code in (200, 201, 202, 204):
            return
        retryable = response.status_code == 429 or response.status_code >= 500
        raise DeliveryFailed(f"director answered {response.status_code}", retryable=retryable)
