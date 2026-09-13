"""A shipping notice: extract carrier, tracking and arrival, then propose the new date.

The model reads the notice; a carrier adapter may refine the arrival date;
the proposal itself is arithmetic against the order lines (source
``tracking``), reviewed by a person before anything is written.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

from logistics.carriers import CarrierTracker
from logistics.nodes.common import context_of, finish
from logistics.state import Node
from sc_core.i18n import Language, t
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import ChangeProposal, ProposedChange, ShipmentInfo
from supplier_comms.render import inbound_context

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
REVIEW_THRESHOLD = 0.7


def make_extract_shipment(
    chat: ChatCompleter,
    tracker: CarrierTracker,
    *,
    langfuse: LangfuseCfg | None,
    today: Callable[[], date],
) -> Node:
    async def extract_shipment(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        formats = get_prompt("formats", cfg=langfuse).compile(po_name=ctx.name)
        prompt = get_prompt("extract_shipment", local_dir=PROMPTS_DIR, cfg=langfuse)
        info = await complete_structured(
            chat,
            [
                system("\n\n".join([formats, prompt.text])),
                user(
                    inbound_context(
                        ctx,
                        today(),
                        state.get("inbound_text") or "",
                        state.get("attachments_text") or [],
                    )
                ),
            ],
            ShipmentInfo,
            name="logistics.extract_shipment",
            metadata={"prompt": prompt.name, "prompt_version": prompt.version, "po_name": ctx.name},
        )
        if info.tracking_number:
            tracked = await tracker.track(info.carrier, info.tracking_number)
            if tracked is not None and tracked.eta_date is not None:
                info = info.model_copy(
                    update={"eta_date": tracked.eta_date, "confidence": max(info.confidence, 0.9)}
                )
                logger.bind(tracking=info.tracking_number).info("arrival refined by the carrier")
        logger.bind(
            po_name=ctx.name,
            carrier=info.carrier,
            tracking=info.tracking_number,
            eta=str(info.eta_date),
        ).info("shipment extracted")
        return {"shipment": info.model_dump(mode="json")}

    return extract_shipment


def build_eta_proposal(
    ctx: Any, info: ShipmentInfo, language: Language = "en"
) -> ChangeProposal | None:
    """Date changes for every line whose planned date differs from the notice; ``None`` when
    the notice gives no date or confirms what Odoo already has."""
    if info.eta_date is None:
        return None
    low = info.confidence < REVIEW_THRESHOLD
    changes = [
        ProposedChange(
            po_line_id=line.id,
            product=line.product,
            field="date_planned",
            before=line.date_planned.isoformat() if line.date_planned else None,
            after=info.eta_date.isoformat(),
            source="tracking",
            confidence=info.confidence,
            needs_review=low,
            review_reason=t("shipment.low_confidence", language) if low else None,
        )
        for line in ctx.lines
        if line.date_planned != info.eta_date
    ]
    if not changes:
        return None
    summary = t(
        "shipment.summary",
        language,
        date=info.eta_date.isoformat(),
        n=len(changes),
        carrier=info.carrier or "-",
        tracking=info.tracking_number or "-",
    )
    return ChangeProposal(changes=changes, summary=summary[:500])


def make_propose_eta(*, language: Language = "en") -> Node:
    async def propose_eta(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        info = ShipmentInfo.model_validate(state["shipment"])
        proposal = build_eta_proposal(ctx, info, language)
        if proposal is None:
            what = (
                t("shipment.no_date", language, po=ctx.name)
                if info.eta_date is None
                else t("shipment.unchanged", language, po=ctx.name, date=info.eta_date.isoformat())
            )
            logger.bind(po_name=ctx.name).info("notice needs no date change")
            return finish("no_action", what)
        logger.bind(po_name=ctx.name, changes=len(proposal.changes)).info("arrival date proposed")
        return {"proposal": proposal.model_dump(mode="json")}

    return propose_eta
