"""Approvals announced in a Microsoft Teams channel through an incoming webhook.

Optional: set ``SC__DIRECTOR__TEAMS_WEBHOOK_URL`` and every approval an agent
asks for is posted as a card with a link back to the Control Tower. The card
carries the summary a person would read in the inbox and never a mail body.
A failed post is logged and forgotten: Teams is a mirror, not the record.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx
from loguru import logger

KIND_LABELS: dict[str, str] = {
    "send_email": "Email to a supplier",
    "po_change": "Order change",
    "planning_run": "Planning run",
    "escalation": "Escalation",
    "vendor_bill": "Vendor bill",
    "supplier_score": "Supplier scorecards",
    "autonomy_change": "Autonomy change",
    "award": "Quote round award",
    "negotiation_offer": "Counter-offer",
    "partner_create": "New supplier",
    "internal_request": "Internal request",
    "price_list_update": "Price list update",
}


@runtime_checkable
class ApprovalNotifier(Protocol):
    async def approval_requested(
        self,
        *,
        approval_id: int,
        kind: str,
        summary: str,
        po_name: str | None = None,
        case_code: str | None = None,
        agent: str | None = None,
    ) -> None: ...


def approval_card(
    *,
    approval_id: int,
    kind: str,
    summary: str,
    control_tower_url: str,
    po_name: str | None = None,
    case_code: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """The Teams message (an Adaptive Card): title, facts, one button to the inbox."""
    facts = [{"title": "Kind", "value": KIND_LABELS.get(kind, kind)}]
    if po_name:
        facts.append({"title": "Order", "value": po_name})
    if case_code:
        facts.append({"title": "Case", "value": case_code})
    if agent:
        facts.append({"title": "Asked by", "value": agent})
    link = f"{control_tower_url.rstrip('/')}/approvals?id={approval_id}"
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "size": "Medium",
                "weight": "Bolder",
                "text": f"Approval #{approval_id} needs a decision",
            },
            {"type": "TextBlock", "text": summary[:500], "wrap": True},
            {"type": "FactSet", "facts": facts},
        ],
        "actions": [{"type": "Action.OpenUrl", "title": "Open in the Control Tower", "url": link}],
    }
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
        ],
    }


class TeamsNotifier:
    def __init__(
        self,
        webhook_url: str,
        *,
        control_tower_url: str,
        http: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._url = webhook_url
        self._ui = control_tower_url or "http://localhost:8010"
        self._http = http or httpx.AsyncClient(timeout=timeout_seconds)

    async def approval_requested(
        self,
        *,
        approval_id: int,
        kind: str,
        summary: str,
        po_name: str | None = None,
        case_code: str | None = None,
        agent: str | None = None,
    ) -> None:
        payload = approval_card(
            approval_id=approval_id,
            kind=kind,
            summary=summary,
            control_tower_url=self._ui,
            po_name=po_name,
            case_code=case_code,
            agent=agent,
        )
        try:
            response = await self._http.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            logger.bind(approval_id=approval_id).warning("teams post failed: {}", exc)
            return
        if response.status_code >= 300:
            logger.bind(approval_id=approval_id, status=response.status_code).warning(
                "teams refused the card"
            )
            return
        logger.bind(approval_id=approval_id, kind=kind).info("approval posted to teams")

    async def aclose(self) -> None:
        await self._http.aclose()


class MemoryNotifier:
    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []

    async def approval_requested(self, **fields: Any) -> None:
        self.posted.append(fields)


__all__ = ["ApprovalNotifier", "MemoryNotifier", "TeamsNotifier", "approval_card"]
