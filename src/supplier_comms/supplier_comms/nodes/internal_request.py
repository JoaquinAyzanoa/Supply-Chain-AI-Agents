"""An employee asks purchasing for something by email (phase 11 S6).

``extract_request`` has the model read what is needed (products, quantities,
date) and then, in code, matches each item to the catalogue (our product code
first, then the closest product name) and finds the reference supplier from
the price lists. The run pauses on an ``internal_request`` approval showing
the matched items. Approved, ``create_request`` writes one RFQ per supplier
with the accepted lines, records the request so status replies can find it,
and answers the requester in the same thread. ``status_reply`` is the
playbook's step that tells the requester the order was confirmed or received.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from loguru import logger

from sc_core.graph import ApprovalGateway, ApprovalRequest, decision_for
from sc_core.i18n import Language, t
from sc_core.infra.internal_requests import InternalRequest
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.mail import po_token
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import InternalRequestData, RequestedItem
from sc_core.schema.autonomy import ActionFacts
from supplier_comms.models import InboundMeta, RequestExtraction
from supplier_comms.nodes.common import esc, fail, finish, task_of
from supplier_comms.nodes.send import resolve_sent_ids
from supplier_comms.ports import AgentPorts
from supplier_comms.state import Node

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
REQUEST_STEP = "internal_request"
ESCALATION_STEP = "request_unreadable"
MATCH_THRESHOLD = 0.6  # the same bar the invoice matcher uses for descriptions
CODE_LIKE = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*(?:-[A-Z0-9]+)+\b")
CODE_IN_NAME = re.compile(r"^\[([^\]]+)\]\s*")

Sleep = Callable[[float], Awaitable[None]]


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalise(a), normalise(b)).ratio()


def plain_name(name: str) -> str:
    """``[CBEA-LHN] Válvula`` -> ``Válvula``."""
    return CODE_IN_NAME.sub("", name or "").strip()


async def match_items(ports: AgentPorts, extraction: RequestExtraction) -> list[RequestedItem]:
    """Each requested item with the product it means and the supplier who lists it."""
    out: list[RequestedItem] = []
    for line in extraction.items:
        item = RequestedItem(
            description=line.description,
            product_ref=line.product_ref,
            qty=line.qty,
            uom=line.uom,
        )
        codes = [line.product_ref] if line.product_ref else []
        codes += CODE_LIKE.findall(line.description.upper())
        product: tuple[int, str] | None = None
        confidence = 0.0
        for code in codes:
            product = await ports.product_by_code(code)
            if product is not None:
                confidence = 1.0
                break
        if product is None:
            best: tuple[float, dict[str, Any]] | None = None
            for candidate in await ports.search_products(line.description, limit=8):
                score = similarity(line.description, plain_name(str(candidate.get("name") or "")))
                if best is None or score > best[0]:
                    best = (score, candidate)
            if best is not None and best[0] >= MATCH_THRESHOLD:
                product = (int(best[1]["id"]), str(best[1].get("name") or line.description))
                confidence = round(best[0], 2)
        if product is not None:
            supplier = await ports.reference_supplier(product[0])
            item = item.model_copy(
                update={
                    "product_id": product[0],
                    "product": product[1],
                    "match_confidence": confidence,
                    "supplier_id": supplier.get("partner_id") if supplier else None,
                    "supplier_name": supplier.get("partner_name") if supplier else None,
                    "unit_price": supplier.get("price") if supplier else None,
                    "currency": supplier.get("currency") if supplier else None,
                }
            )
        out.append(item)
    return out


def request_context(meta: InboundMeta, text: str, today: date) -> str:
    return "\n".join(
        [
            f"Today's date: {today.isoformat()}",
            f"Sender: {meta.sender_address or '-'}",
            "Employee's email:",
            text.strip() or "(empty)",
        ]
    )


def make_extract_request(
    ports: AgentPorts,
    chat: ChatCompleter,
    approvals: ApprovalGateway,
    *,
    langfuse: LangfuseCfg | None,
    today: Callable[[], date],
    language: Language = "en",
) -> Node:
    async def extract_request(state: Any) -> dict[str, Any]:
        meta = InboundMeta.model_validate(state["inbound_meta"])
        text = state.get("inbound_text") or ""
        prompt = get_prompt("extract_internal_request", local_dir=PROMPTS_DIR, cfg=langfuse)
        extraction = await complete_structured(
            chat,
            [system(prompt.text), user(request_context(meta, text, today()))],
            RequestExtraction,
            name="supplier_comms.extract_internal_request",
            metadata={"prompt": prompt.name, "prompt_version": prompt.version},
        )
        items = await match_items(ports, extraction)
        data = InternalRequestData(
            items=items,
            need_date_raw=extraction.need_date_raw,
            need_date=extraction.need_date,
            notes=extraction.notes,
            confidence=extraction.confidence,
        )
        if not items:
            # nothing to buy in the text: a person reads it (no model call decides that)
            update = await approvals.prepare(
                state,
                ESCALATION_STEP,
                ApprovalRequest(
                    kind="escalation",
                    summary=t("request.unreadable", language, sender=meta.sender_address or "-")[
                        :200
                    ],
                    payload={
                        "sender_address": meta.sender_address,
                        "web_link": meta.web_link,
                        "graph_message_id": meta.graph_message_id,
                        "reason": extraction.notes or "no items found in the request",
                    },
                    res_model="sc.approval",
                    res_id=None,
                ),
            )
            pending = (update.get("pending_approvals") or [{}])[0]
            return {
                **update,
                **finish(
                    "escalated",
                    t("request.unreadable", language, sender=meta.sender_address or "-"),
                    internal_request=data.model_dump(mode="json"),
                    escalation_approval_id=pending.get("approval_id"),
                ),
            }
        logger.bind(
            sender=meta.sender_address,
            items=len(items),
            matched=sum(1 for i in items if i.product_id),
            need_date=str(data.need_date),
        ).info("internal request read")
        return {"internal_request": data.model_dump(mode="json")}

    return extract_request


def make_request_approval(
    *, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        data = InternalRequestData.model_validate(state["internal_request"])
        meta = state.get("inbound_meta") or {}
        matched = [i for i in data.items if i.product_id]
        amount = sum((i.unit_price or 0.0) * i.qty for i in matched)
        when = (
            t("request.by_date", language, date=data.need_date.isoformat())
            if data.need_date
            else ""
        )
        return ApprovalRequest(
            kind="internal_request",
            summary=t(
                "request.approval_summary",
                language,
                sender=meta.get("sender_address") or "-",
                n=len(data.items),
                date=when,
            )[:200],
            payload={
                "sender_address": meta.get("sender_address"),
                "graph_message_id": meta.get("graph_message_id"),
                "web_link": meta.get("web_link"),
                "need_date": data.need_date.isoformat() if data.need_date else None,
                "need_date_raw": data.need_date_raw,
                "notes": data.notes,
                "confidence": data.confidence,
                "items": [i.model_dump(mode="json") for i in data.items],
                "unmatched": len(data.items) - len(matched),
                "writes": "one RFQ per supplier with the accepted items, and a reply to the "
                "requester",
            },
            res_model="sc.approval",
            res_id=None,
            review_on_approval=True,
            facts=ActionFacts(
                amount=round(amount, 2) if amount else None,
                confidence=min([data.confidence, *[i.match_confidence for i in matched]])
                if matched
                else data.confidence,
            ),
        )

    return build


def make_create_request(ports: AgentPorts, *, sleep: Sleep, language: Language = "en") -> Node:
    async def create_request(state: Any) -> dict[str, Any]:
        data = InternalRequestData.model_validate(state["internal_request"])
        meta = InboundMeta.model_validate(state["inbound_meta"])
        decision = decision_for(state, REQUEST_STEP)
        details = (decision.details or {}) if decision else {}
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        accepted = details.get("accepted_items")
        wanted = {int(i) for i in accepted} if accepted is not None else None
        override = details.get("partner_id")
        by_supplier: dict[int, list[RequestedItem]] = {}
        no_supplier: list[str] = []
        for index, item in enumerate(data.items):
            if wanted is not None and index not in wanted:
                continue
            if item.product_id is None:
                continue
            partner = int(override) if override else item.supplier_id
            if partner is None:
                no_supplier.append(item.product or item.description)
                continue
            by_supplier.setdefault(partner, []).append(item)
        if not by_supplier:
            return fail(t("request.nothing_to_order", language))
        created: list[tuple[int, str]] = []
        for partner_id, items in by_supplier.items():
            lines = [
                {
                    "product_id": i.product_id,
                    "product_qty": i.qty,
                    "price_unit": i.unit_price,
                    "date_planned": _noon(data.need_date),
                }
                for i in items
            ]
            po_id, po_name = await ports.create_rfq(
                partner_id,
                lines,
                external_ref=f"intreq-{state['case_id']}-{partner_id}",
                origin=t("request.rfq_origin", language, sender=meta.sender_address or "-"),
            )
            created.append((po_id, po_name))
            await ports.post_note(
                po_id,
                t(
                    "request.rfq_note",
                    language,
                    sender=esc(meta.sender_address or "-"),
                    n=len(items),
                    who=esc(who),
                ),
            )
        first_id, first_name = created[0]
        await ports.link_inbound(first_id, meta, case_id=state["case_id"])
        saved = await ports.save_internal_request(
            InternalRequest(
                case_id=state["case_id"],
                graph_message_id=meta.graph_message_id,
                requester_address=meta.sender_address or "",
                po_names=[name for _, name in created],
                need_date=data.need_date,
                items=sum(len(v) for v in by_supplier.values()),
            )
        )
        # the requester hears back in the same thread; the approval covered this reply
        unmatched = [i.description for i in data.items if i.product_id is None] + no_supplier
        html = ack_html(data, [name for _, name in created], unmatched, language)
        ids = await ports.reply_draft(
            meta.graph_message_id, html, headers=po_token.headers_for(first_name, state["case_id"])
        )
        await ports.send_draft(ids.id)
        sent = await resolve_sent_ids(ports, ids, sleep)
        await ports.record_outbound(ids=sent, po_name=first_name, case_id=state["case_id"])
        logger.bind(
            request_id=saved.id, rfqs=[name for _, name in created], sender=meta.sender_address
        ).info("internal request turned into RFQs and acknowledged")
        return finish(
            "applied",
            t(
                "request.applied_summary",
                language,
                sender=meta.sender_address or "-",
                n=len(created),
                rfqs=", ".join(name for _, name in created),
            )
            + (
                t("request.no_supplier", language, items=", ".join(no_supplier))
                if no_supplier
                else ""
            ),
            po_names=[name for _, name in created],
            outbound={"kind": "ack", "to": [meta.sender_address or ""], "subject": "Re: request"},
            sent={"sent_message_id": sent.id, "web_link": sent.web_link},
        )

    return create_request


def make_request_rejected(ports: AgentPorts, *, sleep: Sleep, language: Language = "en") -> Node:
    async def request_rejected(state: Any) -> dict[str, Any]:
        meta = InboundMeta.model_validate(state["inbound_meta"])
        decision = decision_for(state, REQUEST_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = (
            decision.reason if decision and decision.reason else t("common.no_reason", language)
        )
        html = (
            f"<p>{esc(t('request.rejected_html', language, who=who, reason=reason))}</p>"
            f"<p>{esc(t('signature', language))}</p>"
        )
        ids = await ports.reply_draft(
            meta.graph_message_id, html, headers=po_token.headers_for("", state["case_id"])
        )
        await ports.send_draft(ids.id)
        sent = await resolve_sent_ids(ports, ids, sleep)
        return finish(
            "rejected",
            t("request.rejected_summary", language, who=who, reason=reason),
            outbound={"kind": "ack", "to": [meta.sender_address or ""], "subject": "Re: request"},
            sent={"sent_message_id": sent.id, "web_link": sent.web_link},
        )

    return request_rejected


def make_status_reply(ports: AgentPorts, *, sleep: Sleep, language: Language = "en") -> Node:
    async def status_reply(state: Any) -> dict[str, Any]:
        task = task_of(state)
        ctx_data = state.get("po_context") or {}
        po_name = str(ctx_data.get("name") or task.po_name)
        request = await ports.internal_request_for(po_name)
        if request is None:
            return fail(t("request.not_found", language, po=po_name))
        milestone = (task.notes or "").strip().lower() or milestone_of(ctx_data)
        html = status_html(ctx_data, milestone, language)
        ids = await ports.reply_draft(
            request.graph_message_id, html, headers=po_token.headers_for(po_name, state["case_id"])
        )
        await ports.send_draft(ids.id)
        sent = await resolve_sent_ids(ports, ids, sleep)
        await ports.record_outbound(ids=sent, po_name=po_name, case_id=state["case_id"])
        if milestone == "received" and request.id is not None:
            await ports.finish_internal_request(request.id, "done")
        logger.bind(po_name=po_name, milestone=milestone).info("requester told the status")
        return finish(
            "sent",
            t("request.status_summary", language, milestone=milestone, po=po_name),
            outbound={
                "kind": "status",
                "to": [request.requester_address],
                "subject": f"Re: request {po_name}",
            },
            sent={"sent_message_id": sent.id, "web_link": sent.web_link},
        )

    return status_reply


def milestone_of(ctx: dict[str, Any]) -> str:
    state = str(ctx.get("state") or "")
    if state == "cancel":
        return "cancelled"
    lines = ctx.get("lines") or []
    if lines and all(
        float(ln.get("qty_received") or 0) >= float(ln.get("qty") or 0) for ln in lines
    ):
        return "received"
    if state in ("purchase", "done"):
        return "confirmed"
    return "open"


def ack_html(
    data: InternalRequestData, rfqs: list[str], unmatched: list[str], language: Language
) -> str:
    items = "".join(
        f"<li>{esc(f'{i.qty:g}')} {esc(i.uom or '')} {esc(i.product or i.description)}</li>"
        for i in data.items
        if i.product_id
    )
    parts = [
        f"<p>{esc(t('request.ack_intro', language))}</p>",
        f"<ul>{items}</ul>",
        f"<p>{esc(t('request.ack_rfqs', language, rfqs=', '.join(rfqs)))}</p>",
    ]
    if data.need_date:
        parts.append(
            f"<p>{esc(t('request.ack_date', language, date=data.need_date.isoformat()))}</p>"
        )
    if unmatched:
        parts.append(
            f"<p>{esc(t('request.ack_unmatched', language, items=', '.join(unmatched)))}</p>"
        )
    parts.append(f"<p>{esc(t('signature', language))}</p>")
    return "".join(parts)


def status_html(ctx: dict[str, Any], milestone: str, language: Language) -> str:
    po = str(ctx.get("name") or "")
    partner = str(ctx.get("partner_name") or "")
    planned = str(ctx.get("date_planned") or "-")
    key = {
        "confirmed": "request.status_confirmed",
        "received": "request.status_received",
        "cancelled": "request.status_cancelled",
    }.get(milestone, "request.status_open")
    text = t(key, language, po=po, partner=partner, date=planned)
    return f"<p>{esc(text)}</p><p>{esc(t('signature', language))}</p>"


def _noon(day: date | None) -> str | None:
    return f"{day.isoformat()}T12:00:00+00:00" if day else None
