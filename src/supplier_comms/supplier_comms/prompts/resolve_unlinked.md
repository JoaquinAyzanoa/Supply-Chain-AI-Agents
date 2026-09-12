Task: decide which purchase order a supplier email belongs to when the automatic rules could not link it.

You have the email text and the list of candidate orders (supplier, products, quantities, dates). Pick an order only when the email identifies it clearly: it names the order number, its products or quantities, or answers something only that order explains. When no candidate fits, or two fit equally well, answer with an empty po_name.

Give the confidence between 0 and 1 (below 0.7 when you had to assume) and the reason in one sentence a buyer can verify.

Answer only with JSON with the keys po_name, confidence and reason.
