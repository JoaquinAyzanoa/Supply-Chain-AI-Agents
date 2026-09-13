"""Create the Outlook draft, ask for approval, send, record: the supplier agent's
path, without its automatic-send rules (a discrepancy report is always read
by a person first)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from logistics.nodes.common import context_of, esc, finish
from logistics.ports import LogisticsPorts
from logistics.state import Node
from sc_core.graph import ApprovalRequest, decision_for
from sc_core.i18n import Language, t
from sc_core.mail import po_token
from sc_core.mail.models import MessageIds, OutboundMessage

Sleep = Callable[[float], Awaitable[None]]

SEND_STEP = "send_email"


def make_create_draft(ports: LogisticsPorts) -> Node:
    async def create_draft(state: Any) -> dict[str, Any]:
        outbound = dict(state["outbound"] or {})
        if outbound.get("draft_id"):
            return {}
        ctx = context_of(state)
        ids = await ports.create_draft(
            OutboundMessage(
                to=outbound["to"],
                subject=outbound["subject"],
                html_body=state.get("outbound_html") or "",
                headers=po_token.headers_for(ctx.name, state["case_id"]),
                attachments=[],
            )
        )
        outbound.update(
            draft_id=ids.id,
            internet_message_id=ids.internet_message_id,
            conversation_id=ids.conversation_id,
            web_link=ids.web_link,
        )
        logger.bind(po_name=ctx.name, draft_id=ids.id).info("draft created in outlook")
        return {"outbound": outbound}

    return create_draft


def make_send_approval(
    *, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        ctx = context_of(state)
        outbound = state["outbound"] or {}
        return ApprovalRequest(
            kind="send_email",
            summary=t("receipt.approval_summary", language, partner=ctx.partner_name, po=ctx.name),
            payload={
                "to": outbound.get("to"),
                "subject": outbound.get("subject"),
                "html_body": state.get("outbound_html"),
                "po_name": ctx.name,
                "draft_id": outbound.get("draft_id"),
                "web_link": outbound.get("web_link"),
                "attachments": [],
                "reconciliation": state.get("reconciliation"),
            },
            po_id=ctx.id,
        )

    return build


def make_send(
    ports: LogisticsPorts, *, sleep: Sleep, find_attempts: int = 5, language: Language = "en"
) -> Node:
    async def send(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        outbound = state["outbound"] or {}
        draft_ids = MessageIds(
            id=outbound["draft_id"],
            internet_message_id=outbound.get("internet_message_id"),
            conversation_id=outbound.get("conversation_id"),
            web_link=outbound.get("web_link"),
        )
        decision = decision_for(state, SEND_STEP)
        edits = (decision.details or {}) if decision else {}
        edited_subject = edits.get("subject")
        edited_body = edits.get("html_body")
        if edited_subject or edited_body:
            if edited_subject:
                edited_subject = po_token.tag_subject(str(edited_subject), ctx.name)
                outbound = {**outbound, "subject": edited_subject}
            await ports.update_draft(
                draft_ids.id,
                subject=edited_subject,
                html_body=str(edited_body) if edited_body else None,
            )
        await ports.send_draft(draft_ids.id)
        ids = await _sent_ids(ports, draft_ids, sleep, find_attempts)
        await ports.record_outbound(ids=ids, po_name=ctx.name, case_id=state["case_id"])
        await ports.link_outbound(ctx.id, ids, case_id=state["case_id"])
        link = (
            f' <a href="{esc(ids.web_link)}">{t("common.open_outlook", language)}</a>'
            if ids.web_link
            else ""
        )
        await ports.post_note(
            ctx.id,
            t(
                "send.note",
                language,
                label=esc(t("receipt.kind_label", language)),
                to=esc(", ".join(outbound["to"])),
                subject=esc(outbound["subject"]),
                link=link,
            ),
        )
        logger.bind(po_name=ctx.name, message_id=ids.id).info("discrepancy report sent")
        return finish(
            "sent",
            t("receipt.sent_summary", language, partner=ctx.partner_name, po=ctx.name),
            sent={"sent_message_id": ids.id, "web_link": ids.web_link},
            outbound=outbound,
        )

    return send


def make_rejected(ports: LogisticsPorts, *, language: Language = "en") -> Node:
    async def rejected(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        decision = decision_for(state, SEND_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = (
            decision.reason if decision and decision.reason else t("common.no_reason", language)
        )
        await ports.post_note(
            ctx.id, t("send.rejected_note", language, who=esc(who), reason=esc(reason))
        )
        return finish("rejected", t("send.rejected_summary", language, who=who, reason=reason))

    return rejected


async def _sent_ids(
    ports: LogisticsPorts, draft: MessageIds, sleep: Sleep, attempts: int
) -> MessageIds:
    """The sent copy's ids; Outlook needs a moment to file it."""
    if draft.internet_message_id:
        delay = 0.5
        for _ in range(attempts):
            found = await ports.find_sent(draft.internet_message_id)
            if found is not None:
                return found
            await sleep(delay)
            delay = min(delay * 2, 8.0)
    return draft
