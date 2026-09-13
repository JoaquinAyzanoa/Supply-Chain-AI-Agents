"""invoice_match: supplier invoices against the order and what was received.

Clean invoices become draft vendor bills in Odoo (never posted: a person
posts); anything with a variance is held with the table for a person. The
comparison is arithmetic; the model only reads the invoice.
"""

__version__ = "0.1.0"

AGENT_NAME = "invoice_match"
