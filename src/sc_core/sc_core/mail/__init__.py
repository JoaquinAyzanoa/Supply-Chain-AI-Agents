"""Email through Microsoft Graph.

Everything the agents do with email goes through this package: authentication
(``auth``), the Graph client (``graph``, phase 2 story S3), message models,
PO token parsing, body normalisation and PDF text extraction.

Rule: no email body or subject is ever persisted by this project. Outlook is
the record; we store identifiers and links.
"""
