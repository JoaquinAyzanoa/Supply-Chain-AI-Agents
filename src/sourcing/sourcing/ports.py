"""What the graph needs from Odoo, the performance agent and the supplier agent.

Reads: an order's lines and partner, a product's suppliers (the phase 9
ranking merged with the price list and the partner's email), what we last
paid, the current state of an invited RFQ and whether the supplier answered.
Writes (behind an approval, or an RFQ that commits nothing): draft RFQs, the
alternative group, a confirmation, a cancellation, a note. Emails never leave
from here: they go through the supplier agent over A2A.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast

import httpx

from sc_core.a2a import AgentReply
from sc_core.a2a.client import A2AClient
from sc_core.odoo.client import OdooClient
from sc_core.odoo.models import NewOrderLine, RunStatus
from sc_core.odoo.repositories import (
    AgentRunRepo,
    MailLinkRepo,
    PartnerRepo,
    PurchaseOrderRepo,
    SupplierInfoRepo,
)
from sc_core.schema.a2a import QuoteLine, SupplierRanking
from sc_core.shared.errors import ScError
from sourcing import AGENT_NAME
from sourcing.models import BasketLine, OrderRef, PriceEntry, RfqSnapshot, SupplierOption
from sourcing.store import RoundStore


class SourcingPorts(RoundStore, Protocol):
    # --- reads -------------------------------------------------------------------
    async def order(self, po_name: str) -> OrderRef | None: ...

    async def basket_for_po(self, po_name: str) -> list[BasketLine]: ...

    async def basket_for_product(self, product_id: int, qty: float) -> list[BasketLine]: ...

    async def options_for(self, product_ids: list[int]) -> list[SupplierOption]:
        """Who could supply the basket, best first (ranked, listed, with an email or not)."""
        ...

    async def price_entries(self, product_ids: list[int]) -> list[PriceEntry]: ...

    async def last_paid(self, product_id: int) -> float | None: ...

    async def read_rfq(self, po_id: int) -> RfqSnapshot | None: ...

    # --- writes ------------------------------------------------------------------
    async def create_rfq(
        self, partner_id: int, lines: list[BasketLine], *, external_ref: str, origin: str
    ) -> tuple[int, str]: ...

    async def group_alternatives(self, po_ids: list[int]) -> int | None: ...

    async def confirm_rfq(self, po_id: int) -> str: ...

    async def cancel_rfq(self, po_id: int) -> None: ...

    async def post_note(self, po_id: int, html: str) -> None: ...

    async def drop_lines(self, po_id: int, keep_product_ids: list[int]) -> int:
        """Remove the RFQ lines of products awarded elsewhere; returns how many went."""
        ...

    # --- the supplier agent ------------------------------------------------------
    async def send_task(self, agent: str, task_json: str, *, case_id: str) -> AgentReply: ...

    # --- runs --------------------------------------------------------------------
    async def start_run(
        self, *, run_id: str, case_id: str, model: str | None, trace_url: str | None
    ) -> None: ...

    async def finish_run(
        self, run_id: str, *, status: str, summary: str, usage: dict[str, Any] | None = None
    ) -> None: ...


class LiveSourcingPorts:
    def __init__(
        self,
        *,
        odoo: OdooClient,
        store: RoundStore,
        purchase_orders: PurchaseOrderRepo,
        partners: PartnerRepo,
        mail_links: MailLinkRepo,
        supplier_info: SupplierInfoRepo,
        agent_runs: AgentRunRepo,
        supplier_comms: A2AClient,
        performance_url: str,
        token: str,
    ) -> None:
        self._odoo = odoo
        self._store = store
        self._pos = purchase_orders
        self._partners = partners
        self._links = mail_links
        self._supplier_info = supplier_info
        self._runs = agent_runs
        self._supplier_comms = supplier_comms
        self._performance = httpx.AsyncClient(
            base_url=performance_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )

    # the round store is delegated so the graph sees one object
    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    # --- reads -------------------------------------------------------------------

    async def order(self, po_name: str) -> OrderRef | None:
        po = await self._pos.get_by_name(po_name)
        if po is None:
            return None
        return OrderRef(
            po_id=po.id,
            po_name=po.name,
            partner_id=po.partner_id.id,
            partner_name=po.partner_id.name,
            state=po.state,
            currency=po.currency_id.name if po.currency_id else None,
            currency_id=po.currency_id.id if po.currency_id else None,
        )

    async def basket_for_po(self, po_name: str) -> list[BasketLine]:
        po = await self._pos.get_by_name(po_name)
        if po is None:
            return []
        basket: list[BasketLine] = []
        for line in await self._pos.lines(po.id):
            if line.product_id is None or line.product_qty <= 0:
                continue
            basket.append(
                BasketLine(
                    product_id=line.product_id.id,
                    product=line.product_id.name,
                    qty=line.product_qty,
                    last_paid=await self.last_paid(line.product_id.id),
                    currency=line.currency_id.name if line.currency_id else None,
                )
            )
        return basket

    async def basket_for_product(self, product_id: int, qty: float) -> list[BasketLine]:
        rows = await self._odoo.read("product.product", [product_id], ["display_name"])
        if not rows:
            return []
        return [
            BasketLine(
                product_id=product_id,
                product=str(rows[0].get("display_name") or product_id),
                qty=qty,
                last_paid=await self.last_paid(product_id),
            )
        ]

    async def options_for(self, product_ids: list[int]) -> list[SupplierOption]:
        rankings = await self._rankings(product_ids)
        by_partner: dict[int, SupplierOption] = {}
        listed: dict[int, list[int]] = {}
        for ranking in rankings:
            for entry in ranking.suppliers:
                listed.setdefault(entry.partner_id, []).append(ranking.product_id)
                current = by_partner.get(entry.partner_id)
                option = SupplierOption(
                    partner_id=entry.partner_id,
                    partner_name=entry.partner_name,
                    score=entry.score,
                    rank=entry.rank,
                    price=entry.price,
                    currency=entry.currency,
                    min_qty=entry.min_qty,
                    lead_days=entry.promised_lead_days or None,
                    why=entry.why,
                )
                if current is None or option.rank < current.rank:
                    by_partner[entry.partner_id] = option
        options: list[SupplierOption] = []
        for option in by_partner.values():
            emails = await self._partners.emails_of(option.partner_id)
            confirmed = await self._pos.count(
                [["partner_id", "=", option.partner_id], ["state", "in", ["purchase", "done"]]]
            )
            options.append(
                option.model_copy(
                    update={
                        "has_email": bool(emails),
                        "first_time": confirmed == 0,
                        "product_ids": sorted(set(listed.get(option.partner_id, []))),
                    }
                )
            )
        options.sort(key=lambda o: (o.rank or 99, o.partner_name))
        return options

    async def _rankings(self, product_ids: list[int]) -> list[SupplierRanking]:
        if not product_ids:
            return []
        ids = ",".join(str(pid) for pid in sorted(set(product_ids)))
        try:
            response = await self._performance.get("/performance/rank", params={"product_ids": ids})
        except httpx.HTTPError as exc:
            raise ScError(
                "the performance agent did not answer", details={"error": str(exc)}
            ) from exc
        if response.status_code != 200:
            raise ScError(
                f"the performance agent answered {response.status_code}",
                details={"status": response.status_code},
            )
        return [SupplierRanking.model_validate(row) for row in response.json()]

    async def price_entries(self, product_ids: list[int]) -> list[PriceEntry]:
        out: list[PriceEntry] = []
        for product_id in sorted(set(product_ids)):
            for entry in await self._supplier_info.for_product(product_id):
                out.append(
                    PriceEntry(
                        partner_id=entry.partner_id.id,
                        product_id=product_id,
                        price=entry.price,
                        currency=entry.currency_id.name if entry.currency_id else None,
                        min_qty=entry.min_qty,
                        lead_days=entry.delay or None,
                    )
                )
        return out

    async def last_paid(self, product_id: int) -> float | None:
        rows = await self._odoo.search_read(
            "purchase.order.line",
            [["product_id", "=", product_id], ["state", "in", ["purchase", "done"]]],
            ["price_unit"],
            order="date_approve desc, id desc",
            limit=1,
        )
        if not rows:
            return None
        price = rows[0].get("price_unit")
        return float(price) if price else None

    async def read_rfq(self, po_id: int) -> RfqSnapshot | None:
        try:
            po = await self._pos.get(po_id)
        except ScError:
            return None
        lines = [
            QuoteLine(
                product_id=line.product_id.id,
                product=line.product_id.name,
                qty=line.product_qty or 1.0,
                line_id=line.id,
                price_unit=line.price_unit,
                lead_days=_lead_days(po.date_order, line.date_planned),
            )
            for line in await self._pos.lines(po.id)
            if line.product_id is not None
        ]
        sent_at: datetime | None = None
        replied_at: datetime | None = None
        for link in await self._links.for_po(po.id):
            if link.direction == "out" and link.received_at:
                # the request is the first email we sent; a later one of ours (a reminder, a
                # counter-offer) does not make the supplier's earlier answer disappear
                sent_at = min(sent_at, link.received_at) if sent_at else link.received_at
            if link.direction == "in" and link.received_at:
                replied_at = max(replied_at, link.received_at) if replied_at else link.received_at
        if replied_at and sent_at and replied_at < sent_at:
            replied_at = None
        return RfqSnapshot(
            po_id=po.id,
            po_name=po.name,
            partner_id=po.partner_id.id,
            partner_name=po.partner_id.name,
            state=po.state,
            currency=po.currency_id.name if po.currency_id else None,
            lines=lines,
            sent_at=sent_at,
            replied_at=replied_at,
        )

    # --- writes ------------------------------------------------------------------

    async def create_rfq(
        self, partner_id: int, lines: list[BasketLine], *, external_ref: str, origin: str
    ) -> tuple[int, str]:
        po = await self._pos.create_rfq(
            partner_id,
            [
                NewOrderLine(
                    product_id=line.product_id,
                    product_qty=line.qty,
                    date_planned=datetime(
                        line.need_date.year,
                        line.need_date.month,
                        line.need_date.day,
                        12,
                        tzinfo=UTC,
                    )
                    if line.need_date
                    else None,
                )
                for line in lines
            ],
            external_ref=external_ref,
            origin=origin,
        )
        return po.id, po.name

    async def group_alternatives(self, po_ids: list[int]) -> int | None:
        if len(po_ids) < 2:
            return None
        return await self._pos.group_alternatives(po_ids)

    async def confirm_rfq(self, po_id: int) -> str:
        po = await self._pos.confirm(po_id)
        return po.name

    async def cancel_rfq(self, po_id: int) -> None:
        await self._pos.cancel(po_id)

    async def post_note(self, po_id: int, html: str) -> None:
        await self._pos.post_note(po_id, html)

    async def drop_lines(self, po_id: int, keep_product_ids: list[int]) -> int:
        keep = set(keep_product_ids)
        gone = [
            line.id
            for line in await self._pos.lines(po_id)
            if line.product_id is not None and line.product_id.id not in keep
        ]
        if gone:
            await self._odoo.unlink("purchase.order.line", gone)
        return len(gone)

    # --- the supplier agent ------------------------------------------------------

    async def send_task(self, agent: str, task_json: str, *, case_id: str) -> AgentReply:
        if agent != "supplier_comms":
            raise ScError(f"the sourcing agent only talks to supplier_comms, not {agent!r}")
        return await self._supplier_comms.send(task_json, case_id=case_id)

    # --- runs --------------------------------------------------------------------

    async def start_run(
        self, *, run_id: str, case_id: str, model: str | None, trace_url: str | None
    ) -> None:
        await self._runs.start(
            run_id=run_id, agent=AGENT_NAME, case_id=case_id, model=model, trace_url=trace_url
        )

    async def finish_run(
        self, run_id: str, *, status: str, summary: str, usage: dict[str, Any] | None = None
    ) -> None:
        await self._runs.finish(run_id, cast(RunStatus, status), summary[:500], usage)

    async def aclose(self) -> None:
        await self._performance.aclose()
        await self._supplier_comms.aclose()


def _lead_days(ordered: datetime | None, planned: datetime | None) -> int | None:
    if ordered is None or planned is None:
        return None
    ordered = ordered if ordered.tzinfo else ordered.replace(tzinfo=UTC)
    planned = planned if planned.tzinfo else planned.replace(tzinfo=UTC)
    return max(0, (planned - ordered).days)


__all__ = ["LiveSourcingPorts", "SourcingPorts"]
