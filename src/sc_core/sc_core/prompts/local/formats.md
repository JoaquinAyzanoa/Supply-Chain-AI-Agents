Mandatory formats in every structured output:
- Dates: ISO 8601 (YYYY-MM-DD). When the supplier's date is ambiguous (for example "20/10"), read it as day/month and lower the confidence.
- Currency: three-letter ISO code (PEN, USD). Never convert between currencies.
- Quantities: decimal number with a dot; keep the supplier's unit of measure.
- Order identifiers: exactly as they appear in the subject, for example {{po_name}}.
- Confidence: a number between 0 and 1; use less than 0.7 whenever you had to interpret.
