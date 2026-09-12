"""Create the Outlook draft, ask for approval, send, record.

``create_draft`` runs before the approval so the reviewer can open the draft
in Outlook; the draft id in the state makes the node idempotent. ``send``
runs only after an approved decision (or auto-approval for trusted
suppliers): it sends the draft, finds the sent copy for its ids, records
``mail_outbound`` and the outbound ``sc.mail.link`` and leaves a chatter
note. Nothing but identifiers is stored anywhere.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from sc_core.graph import ApprovalRequest, decision_for
from sc_core.i18n import Language, t
from sc_core.mail import po_token
from sc_core.mail.models import Attachment, MessageIds, OutboundMessage
from supplier_comms.nodes.common import context_of, esc, finish, task_of
from supplier_comms.ports import AgentPorts
from supplier_comms.state import Node

Sleep = Callable[[float], Awaitable[None]]

SEND_STEP = "send_email"


def kind_label(kind: str, language: Language) -> str:
    key = (
        f"kind.{kind}"
        if kind in ("rfq", "request_eta", "follow_up", "send_po", "reply")
        else "kind.email"
    )
    return t(key, language)


def make_create_draft(ports: AgentPorts) -> Node:
    async def create_draft(state: Any) -> dict[str, Any]:
        outbound = dict(state["outbound"] or {})
        if outbound.get("draft_id"):
            return {}
        ctx = context_of(state)
        html = state.get("outbound_html") or ""
        headers = po_token.headers_for(ctx.name, state["case_id"])
        attachments = await _attachments(ports, ctx.id, outbound.get("attachments") or [])
        if outbound.get("reply_to_message_id"):
            ids = await ports.reply_draft(
                outbound["reply_to_message_id"], html, headers=headers, attachments=attachments
            )
        else:
            ids = await ports.create_draft(
                OutboundMessage(
                    to=outbound["to"],
                    subject=outbound["subject"],
                    html_body=html,
                    headers=headers,
                    attachments=attachments,
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
    auto_send_partner_ids: frozenset[int], *, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        ctx = context_of(state)
        outbound = state["outbound"] or {}
        label = kind_label(outbound.get("kind", ""), language)
        auto = ctx.partner_id in auto_send_partner_ids
        return ApprovalRequest(
            kind="send_email",
            summary=t(
                "send.approval_summary",
                language,
                label=label,
                partner=ctx.partner_name,
                po=ctx.name,
            ),
            payload={
                "to": outbound.get("to"),
                "subject": outbound.get("subject"),
                "html_body": state.get("outbound_html"),
                "po_name": ctx.name,
                "draft_id": outbound.get("draft_id"),
                "web_link": outbound.get("web_link"),
                "attachments": outbound.get("attachments") or [],
            },
            po_id=ctx.id,
            auto_approve=auto,
            auto_reason=t("send.auto_reason", language) if auto else None,
        )

    return build


def make_send(
    ports: AgentPorts, *, sleep: Sleep, find_attempts: int = 5, language: Language = "en"
) -> Node:
    async def send(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        task = task_of(state)
        outbound = state["outbound"] or {}
        draft_ids = MessageIds(
            id=outbound["draft_id"],
            internet_message_id=outbound.get("internet_message_id"),
            conversation_id=outbound.get("conversation_id"),
            web_link=outbound.get("web_link"),
        )
        await ports.send_draft(draft_ids.id)
        ids = await _sent_ids(ports, draft_ids, sleep, find_attempts)
        await ports.record_outbound(ids=ids, po_name=ctx.name, case_id=state["case_id"])
        await ports.link_outbound(ctx.id, ids, case_id=state["case_id"])
        label = kind_label(outbound.get("kind", ""), language)
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
                label=esc(label),
                to=esc(", ".join(outbound["to"])),
                subject=esc(outbound["subject"]),
                case=esc(state["case_id"]),
                link=link,
            ),
        )
        logger.bind(po_name=ctx.name, kind=task.kind, message_id=ids.id).info("email sent")
        return finish(
            "sent",
            t("send.summary", language, label=label, partner=ctx.partner_name, po=ctx.name),
            sent={"sent_message_id": ids.id, "web_link": ids.web_link},
        )

    return send


def make_rejected(ports: AgentPorts, *, language: Language = "en") -> Node:
    async def rejected(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        decision = decision_for(state, SEND_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = (
            decision.reason if decision and decision.reason else t("common.no_reason", language)
        )
        await ports.post_note(
            ctx.id,
            t(
                "send.rejected_note",
                language,
                who=esc(who),
                reason=esc(reason),
                case=esc(state["case_id"]),
            ),
        )
        return finish("rejected", t("send.rejected_summary", language, who=who, reason=reason))

    return rejected


async def _attachments(ports: AgentPorts, po_id: int, names: list[str]) -> list[Attachment]:
    """The order PDF, fetched from Odoo at draft time (never stored in the state)."""
    out: list[Attachment] = []
    for name in names:
        if name.lower().endswith(".pdf"):
            data = await ports.report_pdf(po_id)
            out.append(
                Attachment(name=name, content_type="application/pdf", size=len(data), data=data)
            )
    return out


async def _sent_ids(
    ports: AgentPorts, draft: MessageIds, sleep: Sleep, attempts: int
) -> MessageIds:
    """The sent copy has a new Graph id; look it up by Message-ID, falling back to the draft ids."""
    if not draft.internet_message_id:
        return draft
    for attempt in range(attempts):
        found = await ports.find_sent(draft.internet_message_id)
        if found is not None:
            return found
        await sleep(1.0 * (attempt + 1))
    logger.warning("sent copy not found by Message-ID; keeping the draft ids")
    return draft
