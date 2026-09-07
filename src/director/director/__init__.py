"""Director: the orchestrator service.

It is the only component that knows the other agents exist. Events from
``mail_sync``, the scheduler and Odoo webhooks enter here and are routed to
the agents over A2A.
"""

__version__ = "0.1.0"
