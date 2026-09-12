"""Agent-related fields and actions on purchase orders.

Field prefix is ``sc_`` (not ``x_``, which Odoo reserves for fields created
from the UI). Everything an agent writes on a PO is visible in the "AI Agent"
tab and explained in the chatter.
"""

import base64

from odoo.exceptions import UserError

from odoo import fields, models

from .sc_event import iso_utc

ETA_SOURCES = [
    ("supplier", "Supplier"),
    ("tracking", "Tracking"),
    ("estimated", "Estimated"),
]


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    sc_external_ref = fields.Char(
        string="Agent external ref",
        index=True,
        copy=False,
        help="Idempotency key set by the agent that created this order. "
        "The same key never creates a second order.",
    )
    sc_eta_source = fields.Selection(
        ETA_SOURCES,
        string="ETA source",
        copy=False,
        help="Where the current planned date came from.",
    )
    sc_eta_confidence = fields.Float(
        string="ETA confidence",
        copy=False,
        help="0..1 confidence the agent assigned to the planned date.",
    )
    sc_needs_human = fields.Boolean(
        string="Needs human",
        default=False,
        copy=False,
        help="An agent could not finish on its own; a pending approval or escalation exists.",
    )
    sc_pending_approval_id = fields.Many2one(
        "sc.approval",
        string="Pending approval",
        copy=False,
        ondelete="set null",
    )
    sc_approval_ids = fields.One2many("sc.approval", "po_id", string="Approvals")
    sc_agent_run_ids = fields.One2many("sc.agent.run", "po_id", string="Agent runs")
    sc_mail_link_ids = fields.One2many("sc.mail.link", "po_id", string="Mail links")

    def action_sc_approve(self):
        self.ensure_one()
        self._sc_require_pending().resolve("approved", self.env.user)

    def action_sc_reject(self):
        self.ensure_one()
        self._sc_require_pending().resolve("rejected", self.env.user)

    def _sc_require_pending(self):
        approval = self.sc_pending_approval_id
        if not approval or approval.status != "pending":
            raise UserError(self.env._("There is no pending approval on this order."))
        return approval

    def sc_emit_confirmed(self):
        """Tell the orchestrator these orders were confirmed (automation rule, state -> purchase).

        The payload is the promise made to the supplier: planned date, amount
        and line count. Phase 9 compares receipts against it.
        """
        emitter = self.env["sc.event.emitter"]
        for order in self.filtered(lambda o: o.state == "purchase"):
            emitter.sc_emit(
                "odoo.purchase_confirmed",
                f"odoo_po_{order.id}_purchase",
                {
                    "po_id": order.id,
                    "po_name": order.name,
                    "partner_id": order.partner_id.id,
                    "date_planned": iso_utc(order.date_planned),
                    "amount_total": order.amount_total,
                    "currency": order.currency_id.name or None,
                    "line_count": len(order.order_line),
                },
                order.id,
                "purchase",
            )
        return True

    def sc_report_pdf(self):
        """Base64 of Odoo's own purchase order report for these orders.

        Report rendering is a private method, so the agents get this public
        wrapper. The result is the same PDF a user prints from the order.
        """
        report = self.env.ref("purchase.action_report_purchase_order")
        pdf, _content_type = report.sudo()._render_qweb_pdf(report.id, self.ids)
        return base64.b64encode(pdf).decode("ascii")


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def sc_log_eta_change(self, source, run_id):
        """Post a chatter note on the order explaining an ETA change made by an agent.

        Called by the agents right after writing ``date_planned`` so the audit
        trail (who, why, which run) sits next to Odoo's own tracking message.
        """
        label = dict(ETA_SOURCES).get(source, source)
        for line in self:
            when = fields.Date.to_string(line.date_planned) if line.date_planned else "-"
            line.order_id.message_post(
                # Keyword names must not collide with env._'s own "source" parameter.
                body=self.env._(
                    "ETA of %(product)s set to %(date)s by agent run %(run)s (origin: %(origin)s)",
                    product=line.product_id.display_name,
                    date=when,
                    run=run_id,
                    origin=label,
                ),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        return True
