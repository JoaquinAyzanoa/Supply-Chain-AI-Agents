"""Supplier 360 (phase 11 S8): everything the desk knows about one supplier, on one page.

The scorecard (from the performance agent), the open orders and their
email timeline (metadata only: direction, date, link), the price list per
product, the quote rounds and the ranking of the products the supplier
sells. The profile people edit has its own endpoint (learning).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException
from fastapi_injector import Injected
from loguru import logger

from director.api.auth import Principal, Viewer
from director.api.board import BoardOrders
from director.api.performance import PerformanceSource
from director.desk.sourcing import SourcingSource
from sc_core.odoo.models import MailLink, SupplierInfo
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError
from sc_core.shared.time import local_today

router = APIRouter(prefix="/suppliers", tags=["suppliers"])
ORDER_WINDOW_DAYS = 180


@runtime_checkable
class SupplierPrices(Protocol):
    async def for_partner(self, partner_id: int) -> list[SupplierInfo]: ...

    async def variant_ids(self, template_ids: list[int]) -> dict[int, int]: ...


@runtime_checkable
class MailLinks(Protocol):
    async def for_po(self, po_id: int) -> list[MailLink]: ...


class PriceRow(StrictModel):
    product_id: int | None = None
    product: str
    supplier_code: str | None = None
    price: float
    currency: str | None = None
    min_qty: float = 0.0
    lead_days: int = 0
    valid_from: date | None = None


class OrderRow(StrictModel):
    po_id: int
    po_name: str
    state: str
    date_planned: date | None = None
    amount_total: float = 0.0
    currency: str | None = None
    receipt_status: str | None = None


class MailRow(StrictModel):
    po_name: str
    direction: str
    at: datetime | None = None
    web_link: str | None = None
    confidence: str | None = None


class ProductRank(StrictModel):
    product_id: int
    product: str
    rank: int | None = None
    suppliers: int = 0
    best_partner_name: str | None = None
    best_score: float | None = None


class Supplier360(StrictModel):
    partner_id: int
    partner_name: str
    score: dict[str, Any] | None = None
    orders: list[OrderRow] = []
    prices: list[PriceRow] = []
    rounds: list[dict[str, Any]] = []
    emails: list[MailRow] = []
    products: list[ProductRank] = []


async def build_supplier(
    partner_id: int,
    *,
    orders: BoardOrders,
    performance: PerformanceSource | None,
    prices: SupplierPrices | None,
    mail_links: MailLinks | None,
    sourcing: SourcingSource | None,
    today: date,
) -> Supplier360 | None:
    name: str | None = None
    score: dict[str, Any] | None = None
    if performance is not None:
        try:
            score = next(
                (
                    r
                    for r in await performance.scores()
                    if int(r.get("partner_id") or 0) == partner_id
                ),
                None,
            )
        except ScError as exc:
            logger.warning("scores unavailable for supplier 360: {}", exc)
        if score:
            name = str(score.get("partner_name") or "")

    rows: list[OrderRow] = []
    mails: list[MailRow] = []
    try:
        board = await orders.board_orders(closed_since=today - timedelta(days=ORDER_WINDOW_DAYS))
    except ScError as exc:
        logger.warning("orders unavailable for supplier 360: {}", exc)
        board = []
    for po in board:
        if po.partner_id.id != partner_id:
            continue
        name = name or po.partner_id.name
        rows.append(
            OrderRow(
                po_id=po.id,
                po_name=po.name,
                state=po.state,
                date_planned=po.date_planned.date() if po.date_planned else None,
                amount_total=po.amount_total,
                currency=po.currency_id.name if po.currency_id else None,
                receipt_status=po.receipt_status,
            )
        )
        if mail_links is not None:
            for link in await mail_links.for_po(po.id):
                mails.append(
                    MailRow(
                        po_name=po.name,
                        direction=link.direction,
                        at=link.received_at,
                        web_link=link.web_link,
                        confidence=link.confidence,
                    )
                )
    rows.sort(key=lambda r: (r.date_planned or date.max, r.po_name), reverse=True)
    mails.sort(key=lambda m: m.at or datetime.min.replace(tzinfo=None), reverse=True)

    price_rows: list[PriceRow] = []
    if prices is not None:
        infos = await prices.for_partner(partner_id)
        # Most price rows sit on the product template; rankings are per variant.
        templates = [
            info.product_tmpl_id.id
            for info in infos
            if info.product_id is None and info.product_tmpl_id is not None
        ]
        variants = await prices.variant_ids(templates) if templates else {}
        for info in infos:
            name = name or info.partner_id.name
            product_id = (
                info.product_id.id
                if info.product_id
                else variants.get(info.product_tmpl_id.id)
                if info.product_tmpl_id
                else None
            )
            price_rows.append(
                PriceRow(
                    product_id=product_id,
                    product=info.product_name
                    or (info.product_id.name if info.product_id else None)
                    or (info.product_tmpl_id.name if info.product_tmpl_id else "?"),
                    supplier_code=info.product_code,
                    price=info.price,
                    currency=info.currency_id.name if info.currency_id else None,
                    min_qty=info.min_qty,
                    lead_days=info.delay,
                    valid_from=info.date_start,
                )
            )

    rounds: list[dict[str, Any]] = []
    if sourcing is not None:
        try:
            rounds = await sourcing.rounds(status=None, partner_id=partner_id)
        except ScError as exc:
            logger.warning("rounds unavailable for supplier 360: {}", exc)

    products: list[ProductRank] = []
    product_ids = sorted({p.product_id for p in price_rows if p.product_id is not None})
    if performance is not None and product_ids:
        try:
            rankings = await performance.rank_many(product_ids[:40])
        except ScError as exc:
            logger.warning("rankings unavailable for supplier 360: {}", exc)
            rankings = []
        labels = {p.product_id: p.product for p in price_rows if p.product_id is not None}
        for ranking in rankings:
            suppliers = ranking.get("suppliers") or []
            mine = next((s for s in suppliers if int(s.get("partner_id") or 0) == partner_id), None)
            best = suppliers[0] if suppliers else None
            products.append(
                ProductRank(
                    product_id=int(ranking["product_id"]),
                    product=labels.get(int(ranking["product_id"]), str(ranking["product_id"])),
                    rank=int(mine["rank"]) if mine and mine.get("rank") is not None else None,
                    suppliers=len(suppliers),
                    best_partner_name=best.get("partner_name") if best else None,
                    best_score=float(best["score"])
                    if best and best.get("score") is not None
                    else None,
                )
            )

    if name is None and not rows and not price_rows:
        return None
    return Supplier360(
        partner_id=partner_id,
        partner_name=name or f"Supplier {partner_id}",
        score=score,
        orders=rows,
        prices=price_rows,
        rounds=rounds,
        emails=mails[:50],
        products=products,
    )


@router.get("/{partner_id}", response_model=Supplier360)
async def supplier_360(
    partner_id: int,
    _: Principal = Viewer,
    orders: BoardOrders = Injected(BoardOrders),  # type: ignore[type-abstract]
    performance: PerformanceSource = Injected(PerformanceSource),  # type: ignore[type-abstract]
    prices: SupplierPrices = Injected(SupplierPrices),  # type: ignore[type-abstract]
    mail_links: MailLinks = Injected(MailLinks),  # type: ignore[type-abstract]
    sourcing: SourcingSource = Injected(SourcingSource),  # type: ignore[type-abstract]
) -> Supplier360:
    view = await build_supplier(
        partner_id,
        orders=orders,
        performance=performance,
        prices=prices,
        mail_links=mail_links,
        sourcing=sourcing,
        today=local_today(),
    )
    if view is None:
        raise HTTPException(status_code=404, detail=f"supplier {partner_id} is not known here")
    return view
