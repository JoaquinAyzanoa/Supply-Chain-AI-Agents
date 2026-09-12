"""Read the mailbox now.

The scheduler polls the inbox on a cron; this is the person's "check for new
emails" button. It posts the same signed tick the scheduler would to the
mail_sync service and answers with what the run found, in a sentence.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx
from fastapi import APIRouter, HTTPException
from fastapi_injector import Injected
from loguru import logger

from director.api.auth import Approver, Principal
from sc_core.a2a.events import EVENT_ID_HEADER, EVENT_TYPE_HEADER, HmacSigner, encode_event
from sc_core.schema.base import StrictModel
from sc_core.schema.events import ScheduledTick
from sc_core.shared.errors import ScError
from sc_core.shared.idempotency import new_id
from sc_core.shared.time import utc_now

router = APIRouter(prefix="/mailbox", tags=["mailbox"])


class MailboxReport(StrictModel):
    status: str
    fetched: int = 0
    linked: int = 0
    unlinked: int = 0
    ignored: int = 0
    errors: int = 0
    linked_po_names: list[str] = []
    message: str


@runtime_checkable
class MailboxSync(Protocol):
    async def run(self, *, requested_by: str) -> dict[str, Any]: ...


class HttpMailboxSync:
    """``POST /jobs/sync`` on mail_sync, signed like a scheduler tick."""

    def __init__(
        self, base_url: str, signer: HmacSigner, *, timeout_seconds: float = 180.0
    ) -> None:
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_seconds)
        self._signer = signer

    async def run(self, *, requested_by: str) -> dict[str, Any]:
        run_id = new_id("run")
        tick = ScheduledTick(
            source="director",
            case_id=run_id,
            job_id="mail_sync",
            run_id=run_id,
            scheduled_at=utc_now(),
            trigger="manual",
        )
        body = encode_event(tick)
        headers = {
            "Content-Type": "application/json",
            "X-SC-Signature": self._signer.sign(body),
            EVENT_TYPE_HEADER: tick.type,
            EVENT_ID_HEADER: tick.event_id,
            "X-SC-Requested-By": requested_by,
        }
        try:
            response = await self._http.post("/jobs/sync", content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ScError(
                "the mailbox service did not answer", details={"error": str(exc)}
            ) from exc
        if response.status_code != 200:
            raise ScError(
                f"the mailbox service answered {response.status_code}",
                details={"status": response.status_code},
            )
        data: dict[str, Any] = response.json()
        return data

    async def aclose(self) -> None:
        await self._http.aclose()


def describe(report: dict[str, Any]) -> str:
    """One sentence a person reads after pressing the button."""
    if report.get("status") == "skipped_locked":
        return "the mailbox is being read right now; try again in a minute"
    fetched = int(report.get("fetched", 0))
    if fetched == 0:
        return "no new emails"
    parts = [f"{fetched} new email{'s' if fetched != 1 else ''}"]
    if linked := int(report.get("linked", 0)):
        names = ", ".join(report.get("linked_po_names") or [])
        parts.append(f"{linked} linked to orders" + (f" ({names})" if names else ""))
    if unlinked := int(report.get("unlinked", 0)):
        parts.append(f"{unlinked} without an order")
    if ignored := int(report.get("ignored", 0)):
        parts.append(f"{ignored} ignored")
    if errors := int(report.get("errors", 0)):
        parts.append(f"{errors} failed")
    return "; ".join(parts)


@router.post("/sync", response_model=MailboxReport)
async def sync_now(
    principal: Principal = Approver,
    mailbox: MailboxSync = Injected(MailboxSync),  # type: ignore[type-abstract]
) -> MailboxReport:
    try:
        report = await mailbox.run(requested_by=principal.email)
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    logger.bind(by=principal.email, status=report.get("status")).info("mailbox read on request")
    return MailboxReport(
        status=str(report.get("status", "ok")),
        fetched=int(report.get("fetched", 0)),
        linked=int(report.get("linked", 0)),
        unlinked=int(report.get("unlinked", 0)),
        ignored=int(report.get("ignored", 0)),
        errors=int(report.get("errors", 0)),
        linked_po_names=list(report.get("linked_po_names") or []),
        message=describe(report),
    )
