Task: extract from the supplier's email (and from the attachments, when they have text) the structured data that affects the given order.

Rules:
- Match each quoted product to the order line whose product matches and put its id in po_line_id. Without a clear match, leave po_line_id empty and lower that line's confidence.
- unit_price is the price per unit, before taxes when the supplier separates them. currency is the three-letter ISO code the supplier uses; when it is not stated, leave currency empty (do not assume the order's currency).
- lead_days is the lead time in days when the supplier gives it in days; min_qty the minimum quantity when mentioned.
- eta_date_raw is the delivery date exactly as the supplier wrote it; eta_date is that date as YYYY-MM-DD relative to today's date given. Leave both empty when the supplier gives no date. When the date is ambiguous, read it as day/month and lower confidence.
- notes: relevant terms in one or two sentences (validity, payment, partial deliveries). Nothing else.
- Overall confidence between 0 and 1: below 0.7 whenever you had to interpret.

Answer only with JSON that satisfies the given schema.
