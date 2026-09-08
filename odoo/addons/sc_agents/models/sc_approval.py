"""A decision a human must take before an agent acts.

Lifecycle: an agent creates the approval (pending) and pauses. A person
resolves it from the purchase order buttons, the Approvals menu, the JSON
controller or the Control Tower. ``resolve`` records who decided and notifies
the agent through a signed HTTP callback so it can resume.

The callback body is canonical JSON; the signature is
``sha256=<hex HMAC-SHA256(callback_secret, body)>`` in ``X-SC-Signature``.
The agent side verifies it before resuming.
"""

import hashlib
import hmac
import json
import logging

import requests
from odoo.exceptions import UserError

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

APPROVAL_KINDS = [
    ("send_email", "Send email"),
    ("po_change", "Purchase order change"),
    ("orderpoint_change", "Reorder rule change"),
    ("planning_run", "Planning run"),
    ("unlinked_mail", "Unlinked mail"),
    ("escalation", "Escalation"),
]
APPROVAL_STATUSES = [
    ("pending", "Pending"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("expired", "Expired"),
]
CALLBACK_TIMEOUT_SECONDS = 10


class ScApproval(models.Model):
    _name = "sc.approval"
    _description = "Agent approval request"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    kind = fields.Selection(APPROVAL_KINDS, required=True, index=True)
    summary = fields.Char(required=True, help="One line a person can decide on.")
    status = fields.Selection(
        APPROVAL_STATUSES, required=True, default="pending", index=True, tracking=True
    )
    res_model = fields.Char(string="Record model")
    res_id = fields.Integer(string="Record id")
    po_id = fields.Many2one(
        "purchase.order", string="Purchase order", ondelete="set null", index=True
    )
    payload_json = fields.Text(
        string="Payload",
        help="What exactly is proposed (email body, list of changes). JSON.",
    )
    requested_by = fields.Char(string="Agent", index=True)
    case_id = fields.Char(string="Case", index=True)
    run_id = fields.Char(string="Run", index=True)
    thread_id = fields.Char(
        string="Agent thread",
        help="Identifier the agent uses to resume its paused graph.",
    )
    resolved_by_id = fields.Many2one("res.users", string="Resolved by", readonly=True)
    resolved_at = fields.Datetime(readonly=True)
    reason = fields.Text(help="Why it was rejected, or notes from the approver.")

    callback_url = fields.Char(string="Callback URL")
    callback_secret = fields.Char(
        string="Callback secret", groups="sc_agents.group_agent,base.group_system"
    )
    callback_status = fields.Selection(
        [("none", "Not sent"), ("sent", "Sent"), ("failed", "Failed")],
        default="none",
        readonly=True,
    )
    callback_error = fields.Char(readonly=True)

    # --- lifecycle ---------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        approvals = super().create(vals_list)
        for approval in approvals:
            approval._sc_attach_to_record()
        return approvals

    def _sc_attach_to_record(self):
        """Mark the purchase order as waiting and leave a chatter note."""
        self.ensure_one()
        if not self.po_id and self.res_model == "purchase.order" and self.res_id:
            self.po_id = self.env["purchase.order"].browse(self.res_id).exists()
        if self.po_id:
            self.po_id.write({"sc_pending_approval_id": self.id, "sc_needs_human": True})
            self.po_id.message_post(
                body=self.env._(
                    "AI agent %(agent)s requests approval (%(kind)s): %(summary)s",
                    agent=self.requested_by or "-",
                    kind=dict(APPROVAL_KINDS).get(self.kind, self.kind),
                    summary=self.summary,
                ),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )

    def action_approve(self):
        for approval in self:
            approval.resolve("approved", self.env.user)

    def action_reject(self):
        for approval in self:
            approval.resolve("rejected", self.env.user)

    def resolve(self, status, user, reason=None):
        """Record the decision and notify the agent. Idempotent on repeat calls."""
        self.ensure_one()
        if status not in ("approved", "rejected", "expired"):
            raise UserError(self.env._("Unknown approval status: %s") % status)
        if self.status != "pending":
            if self.status == status:
                return True  # already resolved the same way: nothing to do
            raise UserError(self.env._("This approval was already resolved as %s.") % self.status)
        self.write(
            {
                "status": status,
                "resolved_by_id": user.id if user else False,
                "resolved_at": fields.Datetime.now(),
                "reason": reason or self.reason,
            }
        )
        self._sc_release_record()
        self._sc_notify_callback()
        return True

    def _sc_release_record(self):
        self.ensure_one()
        po = self.po_id
        if po and po.sc_pending_approval_id == self:
            still_pending = po.sc_approval_ids.filtered(lambda a: a.status == "pending")
            po.write(
                {
                    "sc_pending_approval_id": still_pending[:1].id if still_pending else False,
                    "sc_needs_human": bool(still_pending),
                }
            )

    # --- callback ----------------------------------------------------------

    def _sc_callback_payload(self):
        self.ensure_one()
        return {
            "approval_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "thread_id": self.thread_id or "",
            "case_id": self.case_id or "",
            "run_id": self.run_id or "",
            "resolved_by": self.resolved_by_id.login if self.resolved_by_id else "",
            "resolved_at": fields.Datetime.to_string(self.resolved_at) if self.resolved_at else "",
            "reason": self.reason or "",
        }

    @staticmethod
    def sc_sign(secret, body):
        """HMAC-SHA256 of ``body`` (bytes) with ``secret`` (str) as ``sha256=<hex>``."""
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def _sc_notify_callback(self):
        """POST the decision to the agent. Failure is recorded, never raised.

        The decision itself is already committed by the caller's transaction;
        a dead agent must not undo a human's choice. The agent can also poll
        the approval by id, and ``action_retry_callback`` re-sends it.
        """
        self.ensure_one()
        me = self.sudo()  # callback_secret is restricted to the agent group
        if not me.callback_url:
            return
        body = json.dumps(me._sc_callback_payload(), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        headers = {
            "Content-Type": "application/json",
            "X-SC-Approval-Id": str(me.id),
            "X-SC-Signature": self.sc_sign(me.callback_secret or "", body),
        }
        try:
            response = requests.post(
                me.callback_url, data=body, headers=headers, timeout=CALLBACK_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            me.write({"callback_status": "sent", "callback_error": False})
        except requests.RequestException as exc:
            _logger.warning("approval %s callback failed: %s", me.id, exc)
            me.write({"callback_status": "failed", "callback_error": str(exc)[:500]})

    def action_retry_callback(self):
        for approval in self:
            if approval.status == "pending":
                raise UserError(self.env._("Resolve the approval before sending its callback."))
            approval._sc_notify_callback()
