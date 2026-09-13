"""Vendor bills: tell the orchestrator about the ones people type, and show what the
invoice matching agent concluded on the "AI Agent" tab of the invoice form."""

from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    sc_match_verdict = fields.Selection(
        [("clean", "Matches the order"), ("hold", "Does not match")],
        string="AI check",
        copy=False,
        help="The invoice matching agent's verdict against the order and the receipts.",
    )
    sc_matched_po_id = fields.Many2one(
        "purchase.order", string="Matched order", copy=False, ondelete="set null"
    )
    sc_match_summary = fields.Text(string="AI check summary", copy=False)
    sc_checked_at = fields.Datetime(string="Checked at", copy=False)
    sc_approval_ids = fields.Many2many(
        "sc.approval", compute="_compute_sc_approvals", string="Approvals"
    )

    def _compute_sc_approvals(self):
        """Approvals hung on this bill (the agent's ``res_model``/``res_id`` reference)."""
        approvals = self.env["sc.approval"]
        for move in self:
            move.sc_approval_ids = approvals.search(
                [("res_model", "=", "account.move"), ("res_id", "=", move.id)]
            )

    def sc_emit_bill_created(self):
        """Automation rule (on create): a vendor bill a person created, not one of ours."""
        emitter = self.env["sc.event.emitter"]
        bot = self.env.ref("sc_agents.user_sc_agent_bot", raise_if_not_found=False)
        for move in self.filtered(lambda m: m.move_type == "in_invoice"):
            if bot and move.create_uid == bot:
                continue  # our own draft bills are already matched
            order = move.invoice_line_ids.mapped("purchase_line_id.order_id")[:1]
            emitter.sc_emit(
                "odoo.bill_created",
                f"odoo_bill_{move.id}",
                {
                    "move_id": move.id,
                    "move_name": move.name if move.name and move.name != "/" else None,
                    "partner_id": move.partner_id.id or None,
                    "ref": move.ref or None,
                    "amount_total": move.amount_total,
                    "po_name": order.name or None,
                },
                move.id,
                "created",
            )
        return True

    @api.model
    def sc_post_note(self, move_id, body):
        """A chatter note with real HTML (the RPC path escapes plain strings)."""
        from markupsafe import Markup

        self.browse(move_id).message_post(body=Markup(body))
        return True
