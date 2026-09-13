"""sourcing: the agent that runs a market instead of talking to one supplier.

A quote round invites the best-ranked suppliers through the supplier agent,
collects what they answer, compares landed cost, lead time and score in code
and asks a person to award. A counter-offer is computed within the buyer's
limits and approved before it goes out. Every write to Odoo (an RFQ, a
confirmation, a cancellation, a partner) is deterministic code behind an
approval; the model only writes prose.
"""

__version__ = "0.1.0"

AGENT_NAME = "sourcing"
