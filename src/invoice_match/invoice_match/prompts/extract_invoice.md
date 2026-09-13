Task: read the supplier's invoice (the email and the text of its attachments) and extract it as structured data.

Return JSON with these keys:
- supplier_name: the issuer as printed (null when absent).
- invoice_number: the invoice or receipt number exactly as printed, series included (for example F001-000123). Null when absent.
- invoice_date: ISO date of issue (null when absent). Dates written day/month in Spanish are day first.
- currency: three-letter code (PEN, USD…) or null.
- po_reference: our purchase order number if the invoice or email mentions it (for example P00016), else null.
- lines: one object per billed line with description (as printed), product_ref (the product code as printed, or null), qty (number or null), unit_price (number or null), total (line amount before tax, or null). Skip subtotal, tax and total rows; they are not lines.
- subtotal: amount before taxes (null when absent).
- tax: total taxes (null when absent).
- total: amount due (null when absent).
- confidence: 0 to 1, how sure you are the numbers are right; low when the text is garbled or a column is missing.

Copy numbers as printed; never compute what is not there. Answer only with the JSON.
