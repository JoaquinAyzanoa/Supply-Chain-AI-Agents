"""Vendor bills typed by people: tell the orchestrator so the invoice matching agent checks them."""

from odoo import api, models


class AccountMove(models.Model):
    _inherit = "account.move"

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
