"""What the graph needs: the history from Odoo and the app database, and the writes.

Reads: confirmed orders of the period, their lines and receipts, the first
promise from the case's confirmation snapshot, the emails on each order,
the discrepancy reports the logistics agent sent, the supplier price list.
Writes (after approval): the partner's score fields, the price list's lead
time, the planner's parameters, and the two tables of migration 008.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol, cast

from sc_core.infra.db import Database
from sc_core.odoo.client import OdooClient
from sc_core.odoo.models import RunStatus, SupplierInfo
from sc_core.odoo.repositories import (
    AgentRunRepo,
    MailLinkRepo,
    PurchaseOrderRepo,
    StockMoveLineRepo,
    SupplierInfoRepo,
)
from sc_core.schema.a2a import SupplierScore
from supplier_performance import AGENT_NAME
from supplier_performance.domain.models import MailPair, PriceSeries, ReceivedLine, SupplierHistory


class PerformancePorts(Protocol):
    async def suppliers_with_activity(self, since: date) -> list[tuple[int, str]]: ...

    async def history(
        self, partner_id: int, partner_name: str, *, start: date, end: date
    ) -> SupplierHistory: ...

    async def previous_scores(self) -> dict[int, SupplierScore]: ...

    async def price_list(self, product_id: int) -> list[SupplierInfo]: ...

    async def save_run(self, run_id: str, scores: list[SupplierScore]) -> None: ...

    async def save_observations(self, histories: list[SupplierHistory]) -> int: ...

    async def apply(
        self, run_id: str, scores: list[SupplierScore], *, approval_id: int | None
    ) -> dict[str, int]: ...

    async def start_run(
        self, *, run_id: str, case_id: str, model: str | None, trace_url: str | None
    ) -> None: ...

    async def finish_run(
        self, run_id: str, *, status: str, summary: str, usage: dict[str, Any] | None = None
    ) -> None: ...


class LivePerformancePorts:
    def __init__(
        self,
        *,
        odoo: OdooClient,
        db: Database,
        purchase_orders: PurchaseOrderRepo,
        move_lines: StockMoveLineRepo,
        mail_links: MailLinkRepo,
        supplier_info: SupplierInfoRepo,
        agent_runs: AgentRunRepo,
    ) -> None:
        self._odoo = odoo
        self._db = db
        self._pos = purchase_orders
        self._move_lines = move_lines
        self._links = mail_links
        self._supplier_info = supplier_info
        self._runs = agent_runs

    # --- reads -------------------------------------------------------------------

    async def suppliers_with_activity(self, since: date) -> list[tuple[int, str]]:
        rows = await self._odoo.search_read(
            "purchase.order",
            [["state", "in", ["purchase", "done"]], ["date_approve", ">=", since.isoformat()]],
            ["partner_id"],
            limit=2000,
        )
        seen: dict[int, str] = {}
        for row in rows:
            partner = row.get("partner_id")
            if isinstance(partner, list | tuple) and len(partner) == 2:
                seen.setdefault(int(partner[0]), str(partner[1]))
        return sorted(seen.items(), key=lambda item: item[1])

    async def history(
        self, partner_id: int, partner_name: str, *, start: date, end: date
    ) -> SupplierHistory:
        orders = await self._odoo.search_read(
            "purchase.order",
            [
                ["partner_id", "=", partner_id],
                ["state", "in", ["purchase", "done"]],
                ["date_approve", ">=", start.isoformat()],
                ["date_approve", "<=", end.isoformat()],
            ],
            ["name", "date_approve"],
            order="date_approve asc",
            limit=500,
        )
        lines: list[ReceivedLine] = []
        mails: list[MailPair] = []
        for order in orders:
            confirmed_at = _dt(order.get("date_approve"))
            if confirmed_at is None:
                continue
            promise = await self._first_promise(str(order["name"]))
            po_lines = {line.id: line for line in await self._pos.lines(int(order["id"]))}
            received: dict[int, tuple[float, datetime]] = {}
            for ml in await self._move_lines.received_for_po(int(order["id"])):
                if ml.move_id is None or ml.date is None:
                    continue
                po_line_id = _po_line_of(ml, po_lines)
                if po_line_id is None:
                    continue
                qty, last = received.get(po_line_id, (0.0, ml.date))
                received[po_line_id] = (qty + ml.quantity, max(last, ml.date))
            for line_id, (qty, last) in received.items():
                line = po_lines[line_id]
                promised = promise or (line.date_planned.date() if line.date_planned else None)
                lines.append(
                    ReceivedLine(
                        po_name=str(order["name"]),
                        po_line_id=line_id,
                        product_id=line.product_id.id if line.product_id else None,
                        ordered=line.product_qty,
                        received=qty,
                        confirmed_at=confirmed_at,
                        promised_date=promised,
                        received_at=last,
                    )
                )
            mails.extend(_pairs(await self._links.for_po(int(order["id"]))))
        discrepancies = await self._discrepancy_lines(partner_id, start)
        prices = _price_series(await self._supplier_info.for_partner(partner_id))
        return SupplierHistory(
            partner_id=partner_id,
            partner_name=partner_name,
            period_start=start,
            period_end=end,
            lines=lines,
            mails=mails,
            discrepancy_lines=discrepancies,
            prices=prices,
        )

    async def _first_promise(self, po_name: str) -> date | None:
        """The planned date the order carried when it was confirmed (case snapshot)."""
        row = await self._db.fetch_one(
            "SELECT payload->>'date_planned' AS promised FROM case_events "
            "WHERE kind = 'promise' AND payload->>'po_name' = %s ORDER BY id ASC LIMIT 1",
            (po_name,),
        )
        value = row.get("promised") if row else None
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)).date()
        except ValueError:
            return None

    async def _discrepancy_lines(self, partner_id: int, since: date) -> int:
        rows = await self._odoo.search_read(
            "sc.approval",
            [
                ["requested_by", "=", "logistics"],
                ["kind", "=", "send_email"],
                ["po_id.partner_id", "=", partner_id],
                ["create_date", ">=", since.isoformat()],
            ],
            ["payload_json"],
            limit=500,
        )
        count = 0
        for row in rows:
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except ValueError:
                continue
            count += len((payload.get("reconciliation") or {}).get("discrepancies") or [])
        return count

    async def previous_scores(self) -> dict[int, SupplierScore]:
        rows = await self._db.fetch_all(
            "SELECT DISTINCT ON (partner_id) partner_id, partner_name, period_start, period_end, "
            "otif, lead_time_mean_days, lead_time_sigma_days, promise_drift_days, "
            "response_hours_median, quality_rate, price_cv, score, samples, scorecard, trends "
            "FROM supplier_scores ORDER BY partner_id, computed_at DESC"
        )
        return {int(r["partner_id"]): _score_from_row(r) for r in rows}

    async def price_list(self, product_id: int) -> list[SupplierInfo]:
        return await self._supplier_info.for_product(product_id)

    # --- writes ----------------------------------------------------------------------

    async def save_run(self, run_id: str, scores: list[SupplierScore]) -> None:
        for s in scores:
            await self._db.execute(
                "INSERT INTO supplier_scores (run_id, partner_id, partner_name, period_start, "
                "period_end, otif, lead_time_mean_days, lead_time_sigma_days, promise_drift_days, "
                "response_hours_median, quality_rate, price_cv, score, samples, scorecard, trends) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (run_id, partner_id) DO UPDATE SET score = EXCLUDED.score, "
                "scorecard = EXCLUDED.scorecard, trends = EXCLUDED.trends, "
                "samples = EXCLUDED.samples",
                (
                    run_id,
                    s.partner_id,
                    s.partner_name,
                    s.period_start,
                    s.period_end,
                    s.otif,
                    s.lead_time_mean_days,
                    s.lead_time_sigma_days,
                    s.promise_drift_days,
                    s.response_hours_median,
                    s.quality_rate,
                    s.price_cv,
                    s.score,
                    json.dumps(s.samples),
                    s.scorecard,
                    json.dumps(s.trends),
                ),
            )

    async def save_observations(self, histories: list[SupplierHistory]) -> int:
        written = 0
        for history in histories:
            for line in history.lines:
                lead = max((line.received_at - line.confirmed_at) / timedelta(days=1), 0.0)
                written += await self._db.execute(
                    "INSERT INTO lead_time_observations (partner_id, product_id, po_name, "
                    "po_line_id, confirmed_at, promised_date, received_at, qty_ordered, "
                    "qty_received, lead_time_days, on_time, in_full) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (po_line_id, received_at) DO NOTHING",
                    (
                        history.partner_id,
                        line.product_id,
                        line.po_name,
                        line.po_line_id,
                        line.confirmed_at,
                        line.promised_date,
                        line.received_at,
                        line.ordered,
                        line.received,
                        round(lead, 2),
                        line.promised_date is None or line.received_at.date() <= line.promised_date,
                        line.received + 1e-6 >= line.ordered,
                    ),
                )
        return written

    async def apply(
        self, run_id: str, scores: list[SupplierScore], *, approval_id: int | None
    ) -> dict[str, int]:
        partners = delays = params = 0
        now = datetime.now(UTC)
        for s in scores:
            await self._odoo.write(
                "res.partner",
                [s.partner_id],
                {
                    "sc_score": s.score,
                    "sc_otif": s.otif if s.otif is not None else 0.0,
                    "sc_lead_time_mean": s.lead_time_mean_days or 0.0,
                    "sc_lead_time_std": s.lead_time_sigma_days or 0.0,
                    "sc_scored_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            partners += 1
            if s.lead_time_mean_days is None:
                continue
            days = max(int(round(s.lead_time_mean_days)), 0)
            entries = await self._supplier_info.for_partner(s.partner_id)
            if entries:
                await self._odoo.write(
                    SupplierInfo.ODOO_MODEL, [e.id for e in entries], {"delay": days}
                )
                delays += len(entries)
            product_ids = sorted({e.product_id.id for e in entries if e.product_id is not None})
            for product_id in product_ids:
                params += await self._db.execute(
                    "UPDATE planning_params SET lead_time_mean_days = %s, "
                    "lead_time_sigma_days = %s, source = 'measured', updated_at = now() "
                    "WHERE product_id = %s",
                    (s.lead_time_mean_days, s.lead_time_sigma_days or 0.0, product_id),
                )
        await self._db.execute(
            "UPDATE supplier_scores SET applied_at = now(), approval_id = %s WHERE run_id = %s",
            (approval_id, run_id),
        )
        return {"partners": partners, "price_list_entries": delays, "planning_params": params}

    # --- run log --------------------------------------------------------------------

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


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _po_line_of(ml: Any, po_lines: dict[int, Any]) -> int | None:
    """The order line a counted move line belongs to: the line with its product.

    ``stock.move.line`` carries no link to the order line; receipts have one
    line per product in this business, so the product is the key. A second
    line with the same product would take the first (a known approximation).
    """
    for line in po_lines.values():
        if line.product_id is not None and line.product_id.id == ml.product_id.id:
            return int(line.id)
    return None


def _pairs(links: list[Any]) -> list[MailPair]:
    dated = sorted(
        (link for link in links if link.received_at is not None), key=lambda x: x.received_at
    )
    pairs: list[MailPair] = []
    waiting: MailPair | None = None
    for link in dated:
        if link.direction == "out":
            if waiting is not None:
                pairs.append(waiting)
            waiting = MailPair(sent_at=link.received_at)
        elif waiting is not None:
            pairs.append(waiting.model_copy(update={"replied_at": link.received_at}))
            waiting = None
    if waiting is not None:
        pairs.append(waiting)
    return pairs


def _price_series(entries: list[SupplierInfo]) -> list[PriceSeries]:
    by_product: dict[int | None, list[float]] = {}
    for entry in entries:
        key = (
            entry.product_id.id
            if entry.product_id
            else (entry.product_tmpl_id.id if entry.product_tmpl_id else None)
        )
        by_product.setdefault(key, []).append(entry.price)
    return [PriceSeries(product_id=k, prices=v) for k, v in by_product.items()]


def _score_from_row(row: dict[str, Any]) -> SupplierScore:
    samples = row.get("samples") or {}
    trends = row.get("trends") or []
    return SupplierScore(
        partner_id=int(row["partner_id"]),
        partner_name=str(row["partner_name"]),
        period_start=row["period_start"],
        period_end=row["period_end"],
        otif=_f(row.get("otif")),
        lead_time_mean_days=_f(row.get("lead_time_mean_days")),
        lead_time_sigma_days=_f(row.get("lead_time_sigma_days")),
        promise_drift_days=_f(row.get("promise_drift_days")),
        response_hours_median=_f(row.get("response_hours_median")),
        quality_rate=_f(row.get("quality_rate")),
        price_cv=_f(row.get("price_cv")),
        score=float(row["score"]),
        samples={
            str(k): int(v)
            for k, v in (samples if isinstance(samples, dict) else json.loads(samples)).items()
        },
        scorecard=row.get("scorecard"),
        trends=list(trends if isinstance(trends, list) else json.loads(trends)),
    )


def _f(value: Any) -> float | None:
    return float(value) if value is not None else None
