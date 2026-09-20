"""Counter-offer: plan the number in code, let the model justify it, ask a person,
send it through the supplier agent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from sc_core.graph import ApprovalGateway, ApprovalRequest, decision_for
from sc_core.i18n import Language, t
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import CounterOffer, SupplierCommsResult, SupplierCommsTask
from sc_core.schema.autonomy import ActionFacts
from sc_core.shared.errors import ScError, ValidationFailed
from sourcing.models import Negotiation
from sourcing.negotiation import check_edited_offer, plan_offer
from sourcing.nodes.common import LimitsReader, basket_of, fail, finish, money, order_of, task_of
from sourcing.ports import SourcingPorts
from sourcing.state import Node

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
OFFER_STEP = "negotiation_offer"
ESCALATION_STEP = "escalation"


class JustificationText(BaseModel):
    text: str = Field(min_length=10, max_length=600)


def make_plan_offer(
    ports: SourcingPorts,
    limits: LimitsReader,
    chat: ChatCompleter,
    approvals: ApprovalGateway,
    *,
    langfuse: LangfuseCfg | None,
    language: Language = "en",
) -> Node:
    async def plan(state: Any) -> dict[str, Any]:
        task = task_of(state)
        order = order_of(state)
        if order is None:
            return fail("counter_offer needs an order")
        snapshot = await ports.read_rfq(order.po_id)
        if snapshot is None or not snapshot.lines:
            return fail(f"{order.po_name} has no lines to negotiate")
        line = next(
            (
                ln
                for ln in snapshot.lines
                if (task.product_id is None or ln.product_id == task.product_id) and ln.price_unit
            ),
            None,
        )
        if line is None or not line.price_unit:
            return fail(f"{order.po_name}: no quoted price to negotiate")
        current = await limits()
        basket = {b.product_id: b for b in basket_of(state)}
        last_paid = basket[line.product_id].last_paid if line.product_id in basket else None
        # another supplier's list price counts only when it applies to our quantity
        competing = [
            e.price
            for e in await ports.price_entries([line.product_id])
            if e.partner_id != order.partner_id and e.price and e.min_qty <= line.qty
        ]
        done = [
            n
            for n in await ports.negotiations_for(order.po_name, line.product_id)
            if n.status == "sent"
        ]
        round_no = len(done) + 1
        plan = plan_offer(
            current_price=line.price_unit,
            last_paid=last_paid,
            competing=competing,
            target_override=task.target_price,
            cap_pct=current.cap_pct,
            round_no=round_no,
            max_rounds=current.max_rounds,
            language=language,
        )
        if round_no > current.max_rounds:
            update = await approvals.prepare(
                state,
                ESCALATION_STEP,
                ApprovalRequest(
                    kind="escalation",
                    summary=t("sourcing.rounds_exhausted", language, po=order.po_name, n=len(done))[
                        :200
                    ],
                    payload={
                        "po_name": order.po_name,
                        "product": line.product,
                        "current_price": line.price_unit,
                        "rounds": [n.model_dump(mode="json") for n in done],
                    },
                    po_id=order.po_id,
                ),
            )
            pending = (update.get("pending_approvals") or [{}])[0]
            return {
                **update,
                **finish(
                    "escalated",
                    t("sourcing.rounds_exhausted", language, po=order.po_name, n=len(done)),
                    escalation_approval_id=pending.get("approval_id"),
                ),
            }
        if plan is None:
            target = task.target_price or last_paid or (min(competing) if competing else None)
            return finish(
                "no_action",
                t(
                    "sourcing.at_target",
                    language,
                    product=line.product,
                    po=order.po_name,
                    target=money(target, snapshot.currency),
                ),
            )
        offer = CounterOffer(
            po_name=order.po_name,
            partner_id=order.partner_id,
            partner_name=order.partner_name,
            product_id=line.product_id,
            product=line.product,
            qty=line.qty,
            currency=snapshot.currency,
            current_price=plan.current_price,
            target_price=plan.target_price,
            floor_price=plan.floor_price,
            offered_price=plan.offered_price,
            cap_pct=plan.cap_pct,
            round_no=plan.round_no,
            max_rounds=plan.max_rounds,
            basis=plan.basis,
        )
        offer = offer.model_copy(
            update={
                "justification": await _justify(chat, offer, langfuse=langfuse, language=language)
            }
        )
        saved = await ports.add_negotiation(
            Negotiation(
                id=0,
                case_id=state["case_id"],
                po_id=order.po_id,
                po_name=order.po_name,
                partner_id=order.partner_id,
                product_id=line.product_id,
                round_no=plan.round_no,
                current_price=plan.current_price,
                offered_price=plan.offered_price,
                floor_price=plan.floor_price,
                target_price=plan.target_price,
                created_at=datetime.now(UTC),
            )
        )
        logger.bind(po_name=order.po_name, offered=plan.offered_price, round_no=plan.round_no).info(
            "counter-offer planned"
        )
        return {"offer": offer.model_dump(mode="json"), "negotiation_id": saved.id}

    return plan


async def _justify(
    chat: ChatCompleter, offer: CounterOffer, *, langfuse: LangfuseCfg | None, language: Language
) -> str:
    prompt = get_prompt("justification", local_dir=PROMPTS_DIR, cfg=langfuse)
    facts = (
        f"Product: {offer.product} (qty {offer.qty:g})\n"
        f"Supplier: {offer.partner_name}\n"
        f"Quoted price: {offer.current_price:.2f} {offer.currency or ''}\n"
        f"Our offer: {offer.offered_price:.2f} {offer.currency or ''}\n"
        f"Basis: {offer.basis}\n"
        f"Negotiation round {offer.round_no} of {offer.max_rounds}"
    )
    try:
        text = await complete_structured(
            chat,
            [system(prompt.compile(language=language)), user(facts)],
            JustificationText,
            name="sourcing.justification",
            metadata={"prompt": prompt.name, "prompt_version": prompt.version},
        )
    except ScError as exc:
        logger.warning("justification text skipped: {}", exc)
        return f"Offer of {offer.offered_price:.2f} based on {offer.basis}."
    return text.text


def make_offer_approval(
    *, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        offer = CounterOffer.model_validate(state["offer"])
        order = order_of(state)
        return ApprovalRequest(
            kind="negotiation_offer",
            summary=t(
                "sourcing.offer_summary",
                language,
                partner=offer.partner_name,
                po=offer.po_name,
                product=offer.product,
                offered=money(offer.offered_price, offer.currency),
                current=money(offer.current_price, offer.currency),
                n=offer.round_no,
                max=offer.max_rounds,
            )[:200],
            payload={
                **offer.model_dump(mode="json"),
                "negotiation_id": state.get("negotiation_id"),
                "writes": "an email to the supplier through the supplier agent; no order change",
            },
            po_id=order.po_id if order else None,
            facts=ActionFacts(
                partner_id=offer.partner_id,
                partner_name=offer.partner_name,
                amount=round(offer.offered_price * offer.qty, 2),
                currency=offer.currency,
                change_pct=round(100 * (1 - offer.offered_price / offer.current_price), 2),
            ),
        )

    return build


def make_send_offer(ports: SourcingPorts, *, language: Language = "en") -> Node:
    async def send_offer(state: Any) -> dict[str, Any]:
        offer = CounterOffer.model_validate(state["offer"])
        decision = decision_for(state, OFFER_STEP)
        details = (decision.details or {}) if decision else {}
        negotiation_id = state.get("negotiation_id")
        if details.get("offered_price") is not None:
            try:
                offered = check_edited_offer(
                    float(details["offered_price"]),
                    current_price=offer.current_price,
                    floor_price=offer.floor_price,
                )
            except ValidationFailed as exc:
                return fail(exc.message)
            offer = offer.model_copy(update={"offered_price": offered})
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        approval_id = decision.approval_id if decision else None
        task = SupplierCommsTask(
            kind="counter_offer",
            case_id=f"{state['case_id']}_offer{offer.round_no}",
            po_name=offer.po_name,
            notes=t(
                "sourcing.counter_notes",
                language,
                product=offer.product,
                qty=f"{offer.qty:g}",
                offered=f"{offer.offered_price:.2f}",
                currency=offer.currency or "",
                current=f"{offer.current_price:.2f}",
                basis=offer.basis,
            ),
            pre_approved=f"negotiation_offer #{approval_id} approved by {who}",
        )
        try:
            reply = await ports.send_task(
                "supplier_comms", task.model_dump_json(), case_id=state["case_id"]
            )
            result = SupplierCommsResult.model_validate_json(reply.text)
        except (ScError, ValidationError) as exc:
            if negotiation_id:
                await ports.update_negotiation(
                    negotiation_id, status="failed", approval_id=approval_id
                )
            return fail(f"the counter-offer could not be sent: {exc}")
        status = "sent" if result.status in ("sent", "awaiting_approval") else "failed"
        if negotiation_id:
            await ports.update_negotiation(
                negotiation_id,
                status=status,
                approval_id=approval_id,
                offered_price=offer.offered_price,
            )
        if status == "failed":
            return finish("failed", result.outcome.summary, offer=offer.model_dump(mode="json"))
        return finish(
            "sent",
            t(
                "sourcing.offer_sent",
                language,
                offered=money(offer.offered_price, offer.currency),
                partner=offer.partner_name,
                product=offer.product,
                po=offer.po_name,
            ),
            offer=offer.model_dump(mode="json"),
        )

    return send_offer


def make_offer_rejected(ports: SourcingPorts, *, language: Language = "en") -> Node:
    async def offer_rejected(state: Any) -> dict[str, Any]:
        decision = decision_for(state, OFFER_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = (
            decision.reason if decision and decision.reason else t("common.no_reason", language)
        )
        if state.get("negotiation_id"):
            await ports.update_negotiation(
                state["negotiation_id"],
                status="rejected",
                approval_id=decision.approval_id if decision else None,
            )
        return finish("rejected", t("sourcing.offer_rejected", language, who=who, reason=reason))

    return offer_rejected


__all__ = [
    "ESCALATION_STEP",
    "OFFER_STEP",
    "JustificationText",
    "make_offer_approval",
    "make_offer_rejected",
    "make_plan_offer",
    "make_send_offer",
]
