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
    llm_calls = fields.Integer(string="LLM calls", default=0)
    input_tokens = fields.Integer(default=0)
    output_tokens = fields.Integer(default=0)
    cost_usd = fields.Float(string="Cost (USD)", digits=(12, 6), default=0.0)

    _sql_constraints = [
        ("run_id_unique", "unique(run_id)", "Run ids must be unique."),
    ]

    @api.model
    def sc_finish(self, run_id, status, summary=None, usage=None):
        """Close a run by its id. Returns the number of rows updated (0 or 1).

        ``usage`` adds to the totals (a run paused on an approval finishes twice).
        """
        run = self.search([("run_id", "=", run_id)], limit=1)
        if not run:
            return 0
        values = {
            "status": status,
            "summary": summary or run.summary,
            "finished_at": fields.Datetime.now(),
        }
        if usage:
            values.update(
                {
                    "llm_calls": run.llm_calls + int(usage.get("calls") or 0),
                    "input_tokens": run.input_tokens + int(usage.get("input_tokens") or 0),
                    "output_tokens": run.output_tokens + int(usage.get("output_tokens") or 0),
                    "cost_usd": run.cost_usd + float(usage.get("usd") or 0.0),
                }
            )
        run.write(values)
        return 1
