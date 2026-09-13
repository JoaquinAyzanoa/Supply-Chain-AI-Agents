"""Invite: pick the suppliers, create their RFQs in Odoo, send them through the
supplier agent. Nothing here commits money: an RFQ is a question."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from loguru import logger
from pydantic import ValidationError

from sc_core.i18n import Language, t
from sc_core.schema.a2a import InvitedRfq, SupplierCommsResult, SupplierCommsTask
from sc_core.shared.errors import ScError
from sourcing.models import RoundRfq, SupplierOption
from sourcing.nodes.common import LimitsReader, basket_of, finish, order_of, task_of
from sourcing.ports import SourcingPorts
from sourcing.state import Node


def pick_invitees(
    options: list[SupplierOption],
    *,
    named: list[int],
    excluded: set[int],
    top_n: int,
) -> list[SupplierOption]:
    """Named partners first, then the ranking; suppliers with an email before those without."""
    by_id = {o.partner_id: o for o in options}
    chosen: list[SupplierOption] = []
    for pid in named:
        if pid in excluded or pid in {c.partner_id for c in chosen}:
            continue
        chosen.append(
            by_id.get(pid) or SupplierOption(partner_id=pid, partner_name=f"partner {pid}")
        )
    ranked = [o for o in options if o.partner_id not in excluded and o not in chosen]
    with_email = [o for o in ranked if o.has_email]
    without = [o for o in ranked if not o.has_email]
    for option in [*with_email, *without]:
        if len(chosen) >= max(top_n, len(named)):
            break
        chosen.append(option)
    return chosen


def make_invite(
    ports: SourcingPorts,
    limits: LimitsReader,
    *,
    now: Callable[[], datetime],
    language: Language = "en",
) -> Node:
    async def invite(state: Any) -> dict[str, Any]:
        task = task_of(state)
        order = order_of(state)
        basket = basket_of(state)
        current = await limits()
        excluded = set(task.exclude_partner_ids)
        incumbent = order.partner_id if order else task.partner_id
        # the order's own supplier is not invited again: their RFQ is the invitation
        if incumbent is not None:
            excluded.add(incumbent)
        options = state.get("options")
        if options is None:
            options = [
                o.model_dump(mode="json")
                for o in await ports.options_for([b.product_id for b in basket])
            ]
        invitees = pick_invitees(
            [SupplierOption.model_validate(o) for o in options],
            named=task.partner_ids,
            excluded=excluded,
            top_n=task.max_suppliers or current.top_n,
        )
        products = ", ".join(b.product for b in basket)
        if not invitees:
            return finish("no_action", t("sourcing.nobody", language, product=products))
        deadline = now() + timedelta(days=task.deadline_days or current.deadline_days)
        round_ = await ports.create_round(
            case_id=state["case_id"],
            basket=basket,
            deadline=deadline,
            source_po_name=order.po_name if order else None,
            incumbent_partner_id=incumbent,
            created_by=task.reason or None,
        )
        origin = f"SC round #{round_.id}" + (f" / {order.po_name}" if order else "")
        rfqs: list[RoundRfq] = []
        invited: list[InvitedRfq] = []
        po_ids: list[int] = [order.po_id] if order and order.state in ("draft", "sent") else []
        for option in invitees:
            po_id, po_name = await ports.create_rfq(
                option.partner_id,
                basket,
                external_ref=f"round-{round_.id}-{option.partner_id}",
                origin=origin,
            )
            po_ids.append(po_id)
            rfq = RoundRfq(
                partner_id=option.partner_id,
                partner_name=option.partner_name,
                po_id=po_id,
                po_name=po_name,
                status="created" if option.has_email else "no_email",
            )
            if option.has_email:
                rfq = await _send(ports, rfq, state["case_id"], task.notes, now)
            rfqs.append(rfq)
            invited.append(
                InvitedRfq(
                    partner_id=rfq.partner_id,
                    partner_name=rfq.partner_name,
                    po_name=rfq.po_name,
                    status=rfq.status,
                )
            )
        if order and order.state in ("draft", "sent") and incumbent is not None:
            # the source RFQ stands for the incumbent's invitation
            incumbent_rfq = RoundRfq(
                partner_id=order.partner_id,
                partner_name=order.partner_name,
                po_id=order.po_id,
                po_name=order.po_name,
                status="sent" if order.state == "sent" else "created",
            )
            rfqs.insert(0, incumbent_rfq)
        group_id = await ports.group_alternatives(po_ids)
        await ports.set_rfqs(round_.id, rfqs)
        round_ = await ports.update_round(round_.id, group_id=group_id)
        sent = sum(1 for r in rfqs if r.status == "sent")
        waiting = [r for r in rfqs if r.status == "awaiting_approval"]
        logger.bind(round_id=round_.id, invited=len(invited), sent=sent).info("round started")
        summary = t(
            "sourcing.round_started",
            language,
            round_id=round_.id,
            n=len(invited),
            products=products,
        )
        status = "sent" if sent else ("awaiting_approval" if waiting else "no_action")
        return finish(
            status,  # type: ignore[arg-type]
            summary,
            round=round_.model_dump(mode="json"),
            invited=[i.model_dump(mode="json") for i in invited],
        )

    return invite


async def _send(
    ports: SourcingPorts,
    rfq: RoundRfq,
    case_id: str,
    notes: str | None,
    now: Callable[[], datetime],
) -> RoundRfq:
    thread_id = f"{case_id}_rfq{rfq.partner_id}"
    task = SupplierCommsTask(kind="send_rfq", case_id=thread_id, po_name=rfq.po_name, notes=notes)
    try:
        reply = await ports.send_task("supplier_comms", task.model_dump_json(), case_id=case_id)
        result = SupplierCommsResult.model_validate_json(reply.text)
    except (ScError, ValidationError) as exc:
        logger.bind(po_name=rfq.po_name).warning("invitation failed: {}", exc)
        return rfq.model_copy(update={"status": "failed", "thread_id": thread_id})
    status = {"sent": "sent", "awaiting_approval": "awaiting_approval"}.get(result.status, "failed")
    return rfq.model_copy(
        update={
            "status": status,
            "thread_id": thread_id,
            "sent_at": now() if status == "sent" else None,
        }
    )


__all__ = ["make_invite", "pick_invitees"]
