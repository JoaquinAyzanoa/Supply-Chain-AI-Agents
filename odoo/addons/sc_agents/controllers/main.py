"""JSON endpoints used by the Control Tower and by tests.

Authentication is Odoo's own session (``auth="user"``), so whoever resolves
an approval here is recorded exactly like a click on the form button.
"""

from odoo.exceptions import AccessError
from odoo.http import request

from odoo import http


class ScAgentsController(http.Controller):
    @http.route(
        "/sc_agents/approval/<int:approval_id>/resolve",
        type="json",
        auth="user",
        methods=["POST"],
    )
    def resolve(self, approval_id, status, reason=None):
        approval = request.env["sc.approval"].browse(approval_id).exists()
        if not approval:
            return {"ok": False, "error": "approval not found"}
        approval.check_access("write")
        approval.resolve(status, request.env.user, reason=reason)
        return {"ok": True, "status": approval.status}

    @http.route("/sc_agents/ping", type="json", auth="user", methods=["POST"])
    def ping(self):
        """Cheap authenticated probe used by health checks and tests."""
        if not request.env.user.has_group("base.group_user"):
            raise AccessError("internal users only")
        return {"ok": True, "user": request.env.user.login}
