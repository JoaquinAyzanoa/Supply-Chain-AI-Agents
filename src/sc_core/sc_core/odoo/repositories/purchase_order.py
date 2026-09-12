"""Purchase orders and their lines."""

from __future__ import annotations

import base64
from datetime import date, datetime
from typing import Any

from sc_core.odoo.models import (
    EtaSource,
    NewOrderLine,
    PurchaseOrder,
    PurchaseOrderLine,
    to_odoo_date,
    to_odoo_datetime,
)
from sc_core.odoo.repositories.base import Repo
from sc_core.shared.errors import ExternalServiceError, ValidationFailed

OPEN_STATES = ["purchase"]
RFQ_STATES = ["draft", "sent", "to approve"]


class PurchaseOrderRepo(Repo[PurchaseOrder]):
    model = PurchaseOrder

    # --- reads ----------------------------------------------------------------

    async def get_by_name(self, name: str) -> PurchaseOrder | None:
        return await self.find_one([["name", "=", name]])

    async def find_by_external_ref(self, external_ref: str) -> PurchaseOrder | None:
        return await self.find_one([["sc_external_ref", "=", external_ref]])

    async def lines(self, po_id: int) -> list[PurchaseOrderLine]:
        rows = await self._c.search_read(
            PurchaseOrderLine.ODOO_MODEL,
            [["order_id", "=", po_id]],
            PurchaseOrderLine.odoo_fields(),
            order="id asc",
        )
        return [PurchaseOrderLine.from_odoo(r) for r in rows]

    async def line(self, line_id: int) -> PurchaseOrderLine:
        rows = await self._c.read(
            PurchaseOrderLine.ODOO_MODEL, [line_id], PurchaseOrderLine.odoo_fields()
        )
        if not rows:
            from sc_core.shared.errors import NotFound

            raise NotFound(f"purchase.order.line {line_id} not found")
        return PurchaseOrderLine.from_odoo(rows[0])

    async def open_for_partner(self, partner_id: int) -> list[PurchaseOrder]:
        """Confirmed orders of a supplier that are not fully received, plus open RFQs."""
        return await self.find(
            [
                ["partner_id", "=", partner_id],
                "|",
                "&",
                ["state", "in", OPEN_STATES],
                ["receipt_status", "!=", "full"],
                ["state", "in", RFQ_STATES],
            ],
            order="date_order desc",
        )

    async def late_open_orders(self, as_of: date) -> list[PurchaseOrder]:
        """Confirmed orders whose planned date passed without a full receipt."""
        return await self.find(
            [
                ["state", "in", OPEN_STATES],
                ["date_planned", "<", to_odoo_date(as_of)],
                ["receipt_status", "!=", "full"],
            ],
            order="date_planned asc",
        )

    async def open_rfqs(self, *, sent_before: datetime | None = None) -> list[PurchaseOrder]:
        domain: list[Any] = [["state", "in", ["sent"]]]
        if sent_before is not None:
            domain.append(["write_date", "<", to_odoo_datetime(sent_before)])
        return await self.find(domain, order="write_date asc")

    # --- writes ---------------------------------------------------------------

    async def create_rfq(
        self,
        partner_id: int,
        lines: list[NewOrderLine],
        *,
        external_ref: str,
        origin: str | None = None,
    ) -> PurchaseOrder:
        """Create a draft RFQ. Idempotent: the same ``external_ref`` returns the existing order."""
        if not lines:
            raise ValidationFailed("an RFQ needs at least one line")
        if existing := await self.find_by_external_ref(external_ref):
            return existing
        values: dict[str, Any] = {
            "partner_id": partner_id,
            "sc_external_ref": external_ref,
            "order_line": [(0, 0, line.to_odoo()) for line in lines],
        }
        if origin:
            values["origin"] = origin
        po_id = await self._c.create(self._name, values)
        return await self.get(po_id)

    async def confirm(self, po_id: int) -> PurchaseOrder:
        """RFQ -> purchase order (``button_confirm``)."""
        await self._c.call(self._name, "button_confirm", [po_id])
        return await self.get(po_id)

    async def set_line_date_planned(
        self, line_id: int, new_date: datetime, *, source: EtaSource, run_id: str
    ) -> None:
        """Change a line's planned date and leave the audit note on the order."""
        await self._c.write(
            PurchaseOrderLine.ODOO_MODEL, [line_id], {"date_planned": to_odoo_datetime(new_date)}
        )
        await self._c.call(
            PurchaseOrderLine.ODOO_MODEL,
            "sc_log_eta_change",
            [line_id],
            source=source,
            run_id=run_id,
        )

    async def set_eta_meta(self, po_id: int, *, source: EtaSource, confidence: float) -> None:
        if not 0.0 <= confidence <= 1.0:
            raise ValidationFailed("confidence must be within 0..1")
        await self._write([po_id], {"sc_eta_source": source, "sc_eta_confidence": confidence})

    async def set_needs_human(self, po_id: int, value: bool) -> None:
        await self._write([po_id], {"sc_needs_human": value})

    async def post_note(self, po_id: int, body_html: str) -> int:
        """Internal chatter note (not sent to followers by email)."""
        result = await self._c.call(
            self._name,
            "message_post",
            [po_id],
            body=body_html,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        return int(result[0] if isinstance(result, list) else result)

    async def cancel(self, po_id: int) -> None:
        await self._c.call(self._name, "button_cancel", [po_id])

    async def report_pdf(self, po_id: int) -> bytes:
        """Odoo's own purchase order report ("Orden de Compra") as PDF bytes."""
        encoded = await self._c.call(self._name, "sc_report_pdf", [po_id])
        if not encoded:
            raise ExternalServiceError(
                "odoo returned an empty purchase order report",
                service="odoo",
                details={"po_id": po_id},
                retryable=False,
            )
        return base64.b64decode(encoded)
