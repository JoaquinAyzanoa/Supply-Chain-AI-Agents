Task: read an email an employee sent to the purchasing mailbox and extract what they need.

Return JSON with:
- items: a list, one per product requested, each with description (as written, cleaned), product_ref (a code or part number when the email gives one, else null), qty (a number; 1 when a single unit is implied), uom (unit as written, else null).
- need_date_raw: the date or week as written, or null.
- need_date: the ISO date it means (the Monday when a week is given; today's date is in the context), or null.
- requester_name: the name the sender signs with, or null.
- notes: anything else purchasing must know (project, cost centre, urgency), in one or two sentences, or null.
- confidence: between 0 and 1, lower when quantities or products are unclear.

Extract only what the email says. Do not add products, do not guess quantities that are not there. If nothing is requested (a thank-you, a question about a delivery), return an empty items list with a low confidence.
