"""Plain-text rendering of the order context for prompts and notes."""

from __future__ import annotations

from datetime import date

from sc_core.schema.a2a import SupplierCommsTask
from supplier_comms.models import PoContext


def lines_table(ctx: PoContext) -> str:
    rows = [
        "id | producto | cantidad | unidad | precio unitario | moneda | fecha prevista | recibido"
    ]
    for line in ctx.lines:
        rows.append(
            f"{line.id} | {line.product} | {line.qty:g} | {line.uom or '-'} | "
            f"{line.price_unit:.2f} | {line.currency or ctx.currency or '-'} | "
            f"{line.date_planned.isoformat() if line.date_planned else '-'} | {line.qty_received:g}"
        )
    return "\n".join(rows)


def outbound_context(task: SupplierCommsTask, ctx: PoContext, today: date) -> str:
    parts = [
        f"Fecha de hoy: {today.isoformat()}",
        f"Orden: {ctx.name} (estado: {ctx.state}, id: {ctx.id})",
        f"Proveedor: {ctx.partner_name} (partner_id: {ctx.partner_id})",
        f"Fecha prevista de la orden: {ctx.date_planned.isoformat() if ctx.date_planned else '-'}",
        f"Importe total: {ctx.amount_total:.2f} {ctx.currency or ''}".rstrip(),
        f"Correos previos en este pedido: {ctx.prior_mail_links}",
        "Líneas:",
        lines_table(ctx),
    ]
    if task.days_silent is not None:
        parts.append(f"Días sin respuesta del proveedor: {task.days_silent}")
    if task.notes:
        parts.append(f"Indicaciones del comprador: {task.notes}")
    return "\n".join(parts)
