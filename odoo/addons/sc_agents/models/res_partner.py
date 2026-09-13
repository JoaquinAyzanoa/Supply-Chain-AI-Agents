"""What the supplier performance agent writes on a supplier after an approved weekly run."""

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    sc_score = fields.Float(
        string="Supplier score",
        digits=(5, 2),
        copy=False,
        help="0 to 100 from the weekly scorecard: on-time in-full, lead time stability, "
        "promise drift, reply time and receipt quality.",
    )
    sc_otif = fields.Float(
        string="OTIF",
        digits=(5, 4),
        copy=False,
        help="Share of lines received on time and in full.",
    )
    sc_lead_time_mean = fields.Float(string="Observed lead time (days)", digits=(8, 2), copy=False)
    sc_lead_time_std = fields.Float(string="Lead time std (days)", digits=(8, 2), copy=False)
    sc_scored_at = fields.Datetime(string="Scored at", copy=False, readonly=True)
