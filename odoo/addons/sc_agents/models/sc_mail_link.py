"""Link between an Outlook message and a purchase order.

Only identifiers are stored. The message itself stays in the mailbox; the
``web_link`` opens it there. This is the rule "we never persist email".
"""

from odoo import fields, models


class ScMailLink(models.Model):
    _name = "sc.mail.link"
    _description = "Agent mail link"
    _order = "received_at desc, id desc"
    _rec_name = "graph_message_id"

    po_id = fields.Many2one(
        "purchase.order", string="Purchase order", required=True, ondelete="cascade", index=True
    )
    direction = fields.Selection(
        [("in", "Inbound"), ("out", "Outbound")], required=True, default="in"
    )
    graph_message_id = fields.Char(string="Graph message id", required=True, index=True)
    graph_conversation_id = fields.Char(string="Graph conversation id", index=True)
    internet_message_id = fields.Char(
        string="Internet message id",
        index=True,
        help="RFC 5322 Message-ID, used to match replies through In-Reply-To.",
    )
    received_at = fields.Datetime(string="Received / sent at")
    web_link = fields.Char(string="Open in Outlook")
    case_id = fields.Char(string="Case", index=True)
    confidence = fields.Selection(
        [
            ("exact", "Exact (header, token or thread)"),
            ("sender_single_open_po", "Heuristic (sender with one open order)"),
            ("agent", "Chosen by the agent"),
            ("human", "Chosen by a person"),
        ],
        default="exact",
    )

    _sql_constraints = [
        (
            "graph_message_id_unique",
            "unique(graph_message_id)",
            "This message is already linked to a purchase order.",
        ),
    ]
