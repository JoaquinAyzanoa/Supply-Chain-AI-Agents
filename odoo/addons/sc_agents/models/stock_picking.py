"""Agent-related fields on receipts (used by the logistics agent, phase 9)."""

from odoo import fields, models

from .purchase_order import ETA_SOURCES


class StockPicking(models.Model):
    _inherit = "stock.picking"

    sc_eta_source = fields.Selection(ETA_SOURCES, string="ETA source", copy=False)
    sc_needs_human = fields.Boolean(string="Needs human", default=False, copy=False)
    sc_last_run_id = fields.Char(string="Last agent run", copy=False)
