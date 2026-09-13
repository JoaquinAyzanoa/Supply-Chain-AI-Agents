"""The ``po_change`` approval for a shipping notice and the writes it unlocks.

Approved: the accepted lines get the new planned date (source ``tracking``),
every open receipt on the order moves to it, and the order carries the
notice's facts in a chatter note. Reviewed lines are left for a person.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from loguru import logger

from logistics.nodes.common import context_of, esc, finish
from logistics.ports import LogisticsPorts
from logistics.state import Node
from sc_core.graph import ApprovalRequest, decision_for
from sc_core.i18n import Language, t
from sc_core.schema.a2a import ChangeProposal, ShipmentInfo
from supplier_comms.render import changes_html

ETA_STEP = "po_change"


def make_eta_approval(
    *, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        ctx = context_of(state)
        proposal = ChangeProposal.model_validate(state["proposal"])
        return ApprovalRequest(
            kind="po_change",
            summary=t("changes.approval_summary", language, po=ctx.name, summary=proposal.summary),
            payload={
                "po_name": ctx.name,
                "summary": proposal.summary,
                "changes": [c.model_dump(mode="json") for c in proposal.changes],
                "needs_review": sum(1 for c in proposal.changes if c.needs_review),
                "classification": {"kind": "shipping_notice"},
                "shipment": state.get("shipment"),
            },
            po_id=ctx.id,
        )

    return build


def shipment_html(info: ShipmentInfo, language: Language) -> str:
    facts = [
        (t("shipment.carrier", language), info.carrier),
        (t("shipment.tracking", language), info.tracking_number),
        (t("shipment.shipped", language), info.ship_date.isoformat() if info.ship_date else None),
        (t("shipment.arrival", language), info.eta_date.isoformat() if info.eta_date else None),
    ]
    items = "".join(f"<li>{esc(k)}: {esc(str(v))}</li>" for k, v in facts if v)
    partial = f"<li>{esc(t('shipment.partial', language))}</li>" if info.partial else ""
    return f"<ul>{items}{partial}</ul>"


def make_apply_eta(ports: LogisticsPorts, *, language: Language = "en") -> Node:
    async def apply_eta(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        proposal = ChangeProposal.model_validate(state["proposal"])
        info = ShipmentInfo.model_validate(state["shipment"])
        run_id = state.get("run_id") or "run_unknown"
        decision = decision_for(state, ETA_STEP)
        accepted = (decision.details or {}).get("accepted_line_ids") if decision else None
        wanted = {int(i) for i in accepted} if accepted is not None else None
        applied: list[dict[str, Any]] = []
        for change in proposal.applicable:
            if wanted is not None and change.po_line_id not in wanted:
                continue
            await ports.set_line_date(
                change.po_line_id, date.fromisoformat(change.after), run_id=run_id
            )
            applied.append(change.model_dump(mode="json"))
        if applied and info.eta_date is not None:
            for picking_id in await ports.open_pickings(ctx.id):
                await ports.set_picking_date(picking_id, info.eta_date, run_id=run_id)
            await ports.set_eta_meta(ctx.id, confidence=info.confidence)
        skipped = [
            c.model_dump(mode="json")
            for c in proposal.changes
            if c.needs_review or (wanted is not None and c.po_line_id not in wanted)
        ]
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        await ports.post_note(
            ctx.id,
            t("shipment.applied_note", language, n=len(applied), who=esc(who))
            + shipment_html(info, language)
            + changes_html(applied, language)
            + (
                t("changes.pending_note", language) + changes_html(skipped, language)
                if skipped
                else ""
            ),
        )
        logger.bind(po_name=ctx.name, applied=len(applied), skipped=len(skipped)).info(
            "arrival date applied"
        )
        return finish(
            "applied",
            t("shipment.applied_summary", language, n=len(applied), po=ctx.name)
            + (t("changes.pending_summary", language, n=len(skipped)) if skipped else ""),
        )

    return apply_eta


def make_eta_rejected(ports: LogisticsPorts, *, language: Language = "en") -> Node:
    async def rejected(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        decision = decision_for(state, ETA_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = (
            decision.reason if decision and decision.reason else t("common.no_reason", language)
        )
        await ports.post_note(
            ctx.id, t("changes.rejected_note", language, who=esc(who), reason=esc(reason))
        )
        return finish("rejected", t("changes.rejected_summary", language, who=who, reason=reason))

    return rejected
