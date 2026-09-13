Task: read the supplier's shipping notice about the given order and extract the shipment facts.

Return JSON with these keys:
- carrier: the carrier or courier name as written (null when none).
- tracking_number: the tracking, guide or waybill number exactly as written (null when none).
- ship_date: the day the goods left, ISO date (null when not stated).
- eta_date: the expected arrival day at our warehouse, ISO date (null when not stated). Derive it from phrases like "arrives on", "delivery scheduled for", "3 business days" counted from the ship date or today; when only a transit time is given, say so in eta_date_raw.
- eta_date_raw: the arrival as the supplier wrote it (null when none).
- partial: true when the notice covers only part of the order.
- packing_list: true when a packing list or delivery note is attached or referenced.
- notes: one sentence with anything else that matters (damaged, customs, second shipment), or null.
- confidence: 0 to 1, how sure you are of the arrival date; low when you inferred it.

Never invent a date. Dates written day/month in Spanish are day first. Answer only with the JSON.
