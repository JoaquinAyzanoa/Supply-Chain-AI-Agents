{
    "name": "Supply Chain AI Agents",
    "summary": "Audit fields, approvals, run log and mail links for the AI agents",
    "description": """
Integration points for the Supply Chain AI Agents:

* fields on purchase orders and pickings that record what an agent did and why
* sc.approval: a decision a human must take before an agent acts, with a
  signed callback to the agent when resolved
* sc.agent.run: one row per agent execution, linking to its Langfuse trace
* sc.mail.link: which Outlook message belongs to which purchase order
  (identifiers only, never the message itself)
* a technical user (sc_agent_bot) with least privilege
* signed events to the orchestrator when an order is confirmed, a receipt
  is validated or an approval is resolved
""",
    "version": "18.0.1.2.0",
    "category": "Purchases",
    "author": "Supply Chain AI Agents",
    "license": "LGPL-3",
    "depends": ["purchase", "purchase_stock", "stock", "mail", "base_automation"],
    "data": [
        "security/sc_agents_security.xml",
        "security/ir.model.access.csv",
        "data/sc_agent_user.xml",
        "data/sc_automations.xml",
        "views/sc_approval_views.xml",
        "views/sc_agent_run_views.xml",
        "views/sc_mail_link_views.xml",
        "views/purchase_order_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
