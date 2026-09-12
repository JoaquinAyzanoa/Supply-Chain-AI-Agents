"""Agent-related fields on receipts (used by the logistics agent, phase 9)."""

from odoo import fields, models

from .purchase_order import ETA_SOURCES
from .sc_event import iso_utc


class StockPicking(models.Model):
    _inherit = "stock.picking"

    sc_eta_source = fields.Selection(ETA_SOURCES, string="ETA source", copy=False)
    sc_needs_human = fields.Boolean(string="Needs human", default=False, copy=False)
    sc_last_run_id = fields.Char(string="Last agent run", copy=False)

    def sc_emit_receipt_validated(self):
        """Tell the orchestrator an incoming transfer is done (automation rule, state -> done)."""
        emitter = self.env["sc.event.emitter"]
        for picking in self.filtered(
            lambda p: p.state == "done" and p.picking_type_code == "incoming"
        ):
            order = picking.purchase_id
            emitter.sc_emit(
                "odoo.receipt_validated",
                f"odoo_picking_{picking.id}_done",
                {
                    "picking_id": picking.id,
                    "picking_name": picking.name,
                    "po_id": order.id or None,
                    "po_name": order.name or None,
                    "partner_id": picking.partner_id.id or None,
                    "date_done": iso_utc(picking.date_done),
                },
                picking.id,
                "done",
            )
        return True
