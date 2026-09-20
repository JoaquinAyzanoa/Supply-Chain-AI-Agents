"""Collect the quotes of a round, compare them, ask for the award, apply it.

``collect`` reads every invited RFQ as Odoo shows it now (the supplier agent
wrote the quoted prices on the lines when the quote was approved) and whether
the supplier answered; a supplier who did not is compared on the list price.
``recommend`` lets the model turn the numbers into one paragraph. The
``award`` approval carries the whole comparison; on approval the winner's
RFQ is confirmed (or created and confirmed, for an alternative source) and
the others are declined through the supplier agent and cancelled.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from sc_core.graph import ApprovalRequest, decision_for
from sc_core.i18n import Language, t
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import (
    QuoteComparison,
    QuoteLine,
    SupplierCommsResult,
    SupplierCommsTask,
)
from sc_core.shared.errors import ScError
from sourcing.domain.compare import Offer, Weights, compare
from sourcing.domain.models import BasketLine, Round, RoundRfq
from sourcing.graph.nodes.common import (
    LimitsReader,
    basket_of,
    finish,
    money,
    round_of,
)
from sourcing.graph.state import Node
from sourcing.infra.ports import SourcingPorts

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
AWARD_STEP = "award"


def make_collect(
    ports: SourcingPorts,
    limits: LimitsReader,
    *,
    weights: Weights,
    now: Callable[[], datetime],
    language: Language = "en",
) -> Node:
    async def collect(state: Any) -> dict[str, Any]:
        round_ = round_of(state)
        basket = basket_of(state) or round_.basket
        current = await limits()
        options = {o.partner_id: o for o in await ports.options_for([b.product_id for b in basket])}
        entries = await ports.price_entries([b.product_id for b in basket])
        offers: list[Offer] = []
        rfqs: list[RoundRfq] = []
        for rfq in round_.rfqs:
            refreshed = rfq
            snapshot = await ports.read_rfq(rfq.po_id) if rfq.po_id else None
            if snapshot is not None:
                if snapshot.sent_at and rfq.status in ("created", "awaiting_approval"):
                    refreshed = refreshed.model_copy(
                        update={"status": "sent", "sent_at": snapshot.sent_at}
                    )
                if snapshot.replied_at:
                    refreshed = refreshed.model_copy(update={"replied_at": snapshot.replied_at})
            rfqs.append(refreshed)
            option = options.get(rfq.partner_id)
            if snapshot is not None and refreshed.replied_at is not None:
                offers.append(
                    Offer(
                        partner_id=rfq.partner_id,
                        partner_name=rfq.partner_name,
                        po_name=rfq.po_name,
                        source="reply",
                        currency=snapshot.currency,
                        lines=snapshot.lines,
                        lead_days=max(
                            (
                                line.lead_days
                                for line in snapshot.lines
                                if line.lead_days is not None
                            ),
                            default=option.lead_days if option else None,
                        ),
                        score=option.score if option else None,
                        first_time=option.first_time if option else False,
                    )
                )
            else:
                listed = [e for e in entries if e.partner_id == rfq.partner_id]
                offers.append(
                    Offer(
                        partner_id=rfq.partner_id,
                        partner_name=rfq.partner_name,
                        po_name=rfq.po_name,
                        source="price_list" if listed else "none",
                        currency=listed[0].currency if listed else None,
                        lines=[
                            QuoteLine(
                                product_id=e.product_id,
                                product=next(
                                    (b.product for b in basket if b.product_id == e.product_id), ""
                                ),
                                qty=1.0,
                                price_unit=e.price,
                                lead_days=e.lead_days,
                                min_qty=e.min_qty,
                            )
                            for e in listed
                        ],
                        lead_days=max((e.lead_days or 0 for e in listed), default=None)
                        if listed
                        else None,
                        score=option.score if option else None,
                        first_time=option.first_time if option else False,
                    )
                )
        await ports.set_rfqs(round_.id, rfqs)
        comparison = compare(
            round_id=round_.id,
            basket=basket,
            offers=offers,
            freight_pct=current.freight_pct,
            weights=weights,
            invited=len(rfqs),
            source_po_name=round_.source_po_name,
            language=language,
        )
        if comparison.recommended_partner_id is None:
            await ports.update_round(round_.id, comparison=comparison.model_dump(mode="json"))
            return finish(
                "no_action",
                t("sourcing.nothing_to_compare", language, round_id=round_.id),
                comparison=comparison.model_dump(mode="json"),
            )
        updated = await ports.update_round(
            round_.id, status="comparing", comparison=comparison.model_dump(mode="json")
        )
        logger.bind(round_id=round_.id, priced=sum(1 for q in comparison.quotes if q.total)).info(
            "quotes compared"
        )
        return {
            "round": updated.model_dump(mode="json"),
            "comparison": comparison.model_dump(mode="json"),
            "mode": "round",
        }

    return collect


class RecommendationText(BaseModel):
    text: str = Field(min_length=20, max_length=900)


def make_recommend(
    chat: ChatCompleter, *, langfuse: LangfuseCfg | None, language: Language = "en"
) -> Node:
    """One paragraph a buyer reads before the table; the numbers stay in code."""

    async def recommend(state: Any) -> dict[str, Any]:
        comparison = QuoteComparison.model_validate(state["comparison"])
        prompt = get_prompt("recommendation", local_dir=PROMPTS_DIR, cfg=langfuse)
        try:
            text = await complete_structured(
                chat,
                [system(prompt.compile(language=language)), user(_facts(comparison))],
                RecommendationText,
                name="sourcing.recommendation",
                metadata={"prompt": prompt.name, "prompt_version": prompt.version},
            )
        except ScError as exc:  # the table stands on its own; the paragraph is a courtesy
            logger.warning("recommendation text skipped: {}", exc)
            return {}
        merged = comparison.model_copy(
            update={"recommendation": f"{comparison.recommendation}\n\n{text.text}".strip()}
        )
        return {"comparison": merged.model_dump(mode="json")}

    return recommend


def _facts(comparison: QuoteComparison) -> str:
    lines = [
        "Basket: " + "; ".join(f"{b.product} x {b.qty:g}" for b in comparison.basket),
        f"Freight estimate on top of quoted prices: {comparison.freight_pct:g}%",
        f"Weights: {comparison.weights}",
    ]
    for quote in comparison.quotes:
        detail = "; ".join(quote.reasons) or "no price"
        total = f"{quote.total:,.2f} {quote.currency or ''}".strip() if quote.total else "no total"
        lines.append(
            f"- {quote.partner_name} (rank {quote.rank}, {quote.source}): {total}; {detail}"
        )
    if comparison.last_paid:
        lines.append(f"Last paid per product id: {comparison.last_paid}")
    return "\n".join(lines)


def make_award_approval(
    *, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        comparison = QuoteComparison.model_validate(state["comparison"])
        round_ = round_of(state)
        recommended = next((q for q in comparison.quotes if q.recommended), None)
        products = ", ".join(b.product for b in comparison.basket)
        if state.get("mode") == "direct":
            summary = t(
                "sourcing.alternate_summary",
                language,
                po=round_.source_po_name or "-",
                recommended=recommended.partner_name if recommended else "-",
                products=products,
            )
        else:
            summary = t(
                "sourcing.award_summary",
                language,
                round_id=round_.id,
                products=products,
                recommended=recommended.partner_name if recommended else "-",
                replied=comparison.replied,
                invited=comparison.invited,
            )
        return ApprovalRequest(
            kind="award",
            summary=summary[:200],
            payload={
                "round_id": round_.id,
                "mode": state.get("mode") or "round",
                "source_po_name": round_.source_po_name,
                "comparison": comparison.model_dump(mode="json"),
                "recommended_partner_id": comparison.recommended_partner_id,
                "writes": "confirm the winner's RFQ; decline and cancel the others",
            },
            po_id=await _source_po_id(state),
            res_model="sc.approval",
            res_id=None,
            review_on_approval=True,
        )

    return build


async def _source_po_id(state: dict[str, Any]) -> int | None:
    """The order the award is about: the late order, or the incumbent's RFQ of the round."""
    order = state.get("order") or {}
    if order.get("po_id"):
        return int(order["po_id"])
    round_ = round_of(state)
    for rfq in round_.rfqs:
        if rfq.partner_id == round_.incumbent_partner_id and rfq.po_id:
            return rfq.po_id
    return next((rfq.po_id for rfq in round_.rfqs if rfq.po_id), None)


def winners_of(comparison: QuoteComparison, details: dict[str, Any]) -> dict[int, int]:
    """Product id -> supplier id: the person's split, one supplier for all, or the best per
    line as the comparison found it."""
    if details.get("lines"):
        return {int(k): int(v) for k, v in dict(details["lines"]).items()}
    if details.get("partner_id"):
        chosen = int(details["partner_id"])
        return {b.product_id: chosen for b in comparison.basket}
    return {a.product_id: a.partner_id for a in comparison.line_awards}


def make_award_apply(
    ports: SourcingPorts, *, now: Callable[[], datetime], language: Language = "en"
) -> Node:
    async def award_apply(state: Any) -> dict[str, Any]:
        round_ = round_of(state)
        comparison = QuoteComparison.model_validate(state["comparison"])
        decision = decision_for(state, AWARD_STEP)
        details = (decision.details or {}) if decision else {}
        winners = winners_of(comparison, details)
        if not winners:
            return finish("failed", f"round #{round_.id}: nobody to award to")
        names = {q.partner_id: q.partner_name for q in comparison.quotes}
        unknown = sorted(set(winners.values()) - set(names))
        if unknown:
            return finish("failed", f"partner {unknown[0]} is not part of round #{round_.id}")
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        basket = round_.basket or basket_of(state)
        declined = 0
        awarded: list[str] = []
        first_partner: int | None = None
        if state.get("mode") == "direct":
            by_partner: dict[int, list[BasketLine]] = {}
            for line in basket:
                pid = winners.get(line.product_id)
                if pid is not None:
                    by_partner.setdefault(pid, []).append(line)
            for pid, lines in by_partner.items():
                po_id, _ = await ports.create_rfq(
                    pid,
                    lines,
                    external_ref=f"alt-{round_.id}-{pid}",
                    origin=f"SC alternative source / {round_.source_po_name or ''}".strip(" /"),
                )
                awarded.append(await ports.confirm_rfq(po_id))
                await ports.post_note(
                    po_id, _award_note(round_, comparison, names[pid], who, language)
                )
                first_partner = first_partner or pid
        else:
            # Odoo confirms an RFQ that still has live alternatives only through a wizard:
            # losers are declined and cancelled first, winners trimmed and confirmed last.
            winning: list[tuple[RoundRfq, list[int]]] = []
            for rfq in round_.rfqs:
                if rfq.po_id is None:
                    continue
                asked = rfq.product_ids or [b.product_id for b in basket]
                won = [p for p in asked if winners.get(p) == rfq.partner_id]
                if won:
                    winning.append((rfq, won))
                    continue
                if rfq.status == "sent" or rfq.replied_at is not None:
                    await _decline(ports, rfq, state["case_id"], round_.id, language)
                    declined += 1
                await ports.cancel_rfq(rfq.po_id)
                await ports.update_rfq(round_.id, rfq.partner_id, status="declined")
            for rfq, won in winning:
                assert rfq.po_id is not None
                asked = rfq.product_ids or [b.product_id for b in basket]
                if set(won) != set(asked):
                    await ports.drop_lines(rfq.po_id, won)
                awarded.append(await ports.confirm_rfq(rfq.po_id))
                await ports.post_note(
                    rfq.po_id, _award_note(round_, comparison, rfq.partner_name, who, language)
                )
                first_partner = first_partner or rfq.partner_id
            if not winning:
                return finish("failed", f"round #{round_.id}: no RFQ matches the award")
        await ports.update_round(
            round_.id,
            status="awarded",
            awarded_partner_id=first_partner,
            awarded_po_name=", ".join(awarded),
        )
        logger.bind(round_id=round_.id, awarded=awarded).info("round awarded")
        winners_text = ", ".join(
            f"{names[pid]} ({po})"
            for pid, po in zip(dict.fromkeys(winners.values()), awarded, strict=False)
        )
        return finish(
            "applied",
            t(
                "sourcing.awarded",
                language,
                round_id=round_.id,
                partner=winners_text or names.get(first_partner or 0, "-"),
                po=", ".join(awarded),
                declined=declined,
            ),
            awarded_po_name=awarded[0] if awarded else None,
            awarded_po_names=awarded,
        )

    return award_apply


async def _decline(
    ports: SourcingPorts, rfq: RoundRfq, case_id: str, round_id: int, language: Language
) -> None:
    task = SupplierCommsTask(
        kind="decline_quote",
        case_id=f"{case_id}_decline{rfq.partner_id}",
        po_name=rfq.po_name,
        notes=t("sourcing.decline_notes", language),
        pre_approved=f"round #{round_id} awarded elsewhere; a decline carries no commitment",
    )
    try:
        reply = await ports.send_task("supplier_comms", task.model_dump_json(), case_id=case_id)
        SupplierCommsResult.model_validate_json(reply.text)
    except (ScError, ValidationError) as exc:
        logger.bind(po_name=rfq.po_name).warning("decline not sent: {}", exc)


def _award_note(
    round_: Round, comparison: QuoteComparison, chosen: str, who: str, language: Language
) -> str:
    rows = "".join(
        f"<li>{q.partner_name}: {money(q.total, q.currency)} "
        f"({'; '.join(q.reasons) or 'no price'})</li>"
        for q in comparison.quotes
    )
    head = (
        f"Quote round #{round_.id} awarded to {chosen} by {who}."
        if language == "en"
        else f"Ronda de cotización #{round_.id} adjudicada a {chosen} por {who}."
    )
    return f"<p>{head}</p><ul>{rows}</ul>"


def make_award_rejected(ports: SourcingPorts, *, language: Language = "en") -> Node:
    async def award_rejected(state: Any) -> dict[str, Any]:
        round_ = round_of(state)
        decision = decision_for(state, AWARD_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = (
            decision.reason if decision and decision.reason else t("common.no_reason", language)
        )
        await ports.update_round(round_.id, status="rejected")
        return finish(
            "rejected",
            t("sourcing.award_rejected", language, round_id=round_.id, who=who, reason=reason),
        )

    return award_rejected


def basket_products(basket: list[BasketLine]) -> str:
    return ", ".join(b.product for b in basket)


__all__ = [
    "AWARD_STEP",
    "RecommendationText",
    "basket_products",
    "make_award_apply",
    "make_award_approval",
    "make_award_rejected",
    "make_collect",
    "make_recommend",
]
