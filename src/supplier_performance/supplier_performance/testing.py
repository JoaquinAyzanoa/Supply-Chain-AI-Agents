"""In-memory ports and a synthetic history for the tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sc_core.odoo.models import Ref, SupplierInfo
from sc_core.schema.a2a import SupplierScore
from supplier_performance.models import MailPair, PriceSeries, ReceivedLine, SupplierHistory

__all__ = ["FakePerformancePorts", "hidraulica_history"]


def hidraulica_history(
    *,
    late_lines: int = 1,
    in_full: bool = True,
    replies_hours: tuple[float, ...] = (3.0, 30.0, 6.0),
) -> SupplierHistory:
    """Six received lines over three orders, ``late_lines`` of them after the promise."""
    start, end = date(2025, 9, 1), date(2026, 9, 1)
    lines: list[ReceivedLine] = []
    for i in range(6):
        confirmed = datetime(2026, 3, 1, 10, 0, tzinfo=UTC) + timedelta(days=i * 20)
        promised = confirmed.date() + timedelta(days=10)
        late = i < late_lines
        received_at = confirmed + timedelta(days=13 if late else 9)
        lines.append(
            ReceivedLine(
                po_name=f"P000{20 + i // 2}",
                po_line_id=100 + i,
                product_id=49 + i,
                ordered=10.0,
                received=10.0 if in_full or i % 2 else 8.0,
                confirmed_at=confirmed,
                promised_date=promised,
                received_at=received_at,
            )
        )
    sent = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    mails = [
        MailPair(sent_at=sent + timedelta(days=n), replied_at=sent + timedelta(days=n, hours=h))
        for n, h in enumerate(replies_hours)
    ]
    mails.append(MailPair(sent_at=sent + timedelta(days=10)))  # still unanswered
    return SupplierHistory(
        partner_id=45,
        partner_name="Proveedor Hidraulica",
        period_start=start,
        period_end=end,
        lines=lines,
        mails=mails,
        discrepancy_lines=0,
        prices=[
            PriceSeries(product_id=49, prices=[104.16, 104.16, 106.0]),
            PriceSeries(product_id=50, prices=[88.04]),
        ],
    )


def price_entry(
    partner_id: int, name: str, *, price: float, delay: int, min_qty: float = 0.0, entry_id: int = 1
) -> SupplierInfo:
    return SupplierInfo(
        id=entry_id,
        partner_id=Ref(id=partner_id, name=name),
        product_tmpl_id=Ref(id=1001, name="Válvula"),
        product_id=Ref(id=49, name="[CBEA-LHN] Válvula"),
        min_qty=min_qty,
        price=price,
        currency_id=Ref(id=2, name="USD"),
        delay=delay,
    )


@dataclass
class FakePerformancePorts:
    suppliers: list[tuple[int, str]] = field(default_factory=list)
    histories: dict[int, SupplierHistory] = field(default_factory=dict)
    previous: dict[int, SupplierScore] = field(default_factory=dict)
    price_lists: dict[int, list[SupplierInfo]] = field(default_factory=dict)
    saved_runs: list[tuple[str, list[SupplierScore]]] = field(default_factory=list)
    observations_saved: int = 0
    applied: list[dict[str, Any]] = field(default_factory=list)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def suppliers_with_activity(self, since: date) -> list[tuple[int, str]]:
        return list(self.suppliers)

    async def history(
        self, partner_id: int, partner_name: str, *, start: date, end: date
    ) -> SupplierHistory:
        found = self.histories.get(partner_id)
        if found is None:
            return SupplierHistory(
                partner_id=partner_id, partner_name=partner_name, period_start=start, period_end=end
            )
        return found.model_copy(update={"period_start": start, "period_end": end})

    async def previous_scores(self) -> dict[int, SupplierScore]:
        return dict(self.previous)

    async def price_list(self, product_id: int) -> list[SupplierInfo]:
        return list(self.price_lists.get(product_id, []))

    async def save_run(self, run_id: str, scores: list[SupplierScore]) -> None:
        self.saved_runs.append((run_id, list(scores)))

    async def save_observations(self, histories: list[SupplierHistory]) -> int:
        n = sum(len(h.lines) for h in histories)
        self.observations_saved += n
        return n

    async def apply(
        self, run_id: str, scores: list[SupplierScore], *, approval_id: int | None
    ) -> dict[str, int]:
        self.applied.append(
            {
                "run_id": run_id,
                "partners": [s.partner_id for s in scores],
                "approval_id": approval_id,
            }
        )
        return {
            "partners": len(scores),
            "price_list_entries": len(scores),
            "planning_params": len(scores),
        }

    async def start_run(
        self, *, run_id: str, case_id: str, model: str | None, trace_url: str | None
    ) -> None:
        self.runs[run_id] = {"case_id": case_id, "status": "running"}

    async def finish_run(
        self, run_id: str, *, status: str, summary: str, usage: dict[str, Any] | None = None
    ) -> None:
        self.runs.setdefault(run_id, {})
        self.runs[run_id].update(status=status, summary=summary, usage=usage or {})
