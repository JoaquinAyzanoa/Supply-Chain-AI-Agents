"""logistics: shipment notices, receipt reconciliation and discrepancy reports.

The agent reuses the supplier agent's ports and helpers as a library (one
place knows how to read an email from Graph and send a draft); its graph,
task and result are its own.
"""

__version__ = "0.1.0"

AGENT_NAME = "logistics"
