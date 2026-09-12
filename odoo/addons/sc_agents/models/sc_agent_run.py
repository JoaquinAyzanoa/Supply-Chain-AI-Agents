"""One row per agent execution, so buyers can see what ran on their orders."""

from odoo import api, fields, models

RUN_STATUSES = [
    ("running", "Running"),
    ("sent", "Sent"),
    ("applied", "Applied"),
    ("awaiting_approval", "Awaiting approval"),
    ("rejected", "Rejected"),
    ("no_action", "No action"),
    ("escalated", "Escalated to a person"),
    ("failed", "Failed"),
]


class ScAgentRun(models.Model):
    _name = "sc.agent.run"
    _description = "Agent run"
    _order = "started_at desc, id desc"
    _rec_name = "run_id"

    run_id = fields.Char(required=True, index=True)
    agent = fields.Char(required=True, index=True)
    case_id = fields.Char(string="Case", index=True)
    po_id = fields.Many2one(
        "purchase.order", string="Purchase order", ondelete="set null", index=True
    )
    status = fields.Selection(RUN_STATUSES, required=True, default="running", index=True)
    model = fields.Char(string="LLM model")
    trace_url = fields.Char(string="Trace")
    summary = fields.Text()
    started_at = fields.Datetime(default=fields.Datetime.now)
    finished_at = fields.Datetime()

    _sql_constraints = [
        ("run_id_unique", "unique(run_id)", "Run ids must be unique."),
    ]

    @api.model
    def sc_finish(self, run_id, status, summary=None):
        """Close a run by its id. Returns the number of rows updated (0 or 1)."""
        run = self.search([("run_id", "=", run_id)], limit=1)
        if not run:
            return 0
        run.write(
            {
                "status": status,
                "summary": summary or run.summary,
                "finished_at": fields.Datetime.now(),
            }
        )
        return 1
