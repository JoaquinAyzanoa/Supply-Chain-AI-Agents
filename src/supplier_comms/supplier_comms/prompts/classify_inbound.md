Task: classify the email received from a supplier about the given order.

Possible categories (use exactly one):
- quotation: the supplier gives prices, lead times or terms for the products (quotation, proforma, price list).
- eta_update: the supplier confirms, changes or delays the delivery date, or reports a partial delivery.
- shipping_notice: the supplier says the goods were dispatched or are on their way: carrier, tracking or waybill number, dispatch date, expected arrival, packing list.
- question: the supplier asks something or requests a clarification and expects an answer from us.
- other: automatic reply, out of office, empty acknowledgement, advertising, or anything that needs no action.

If the email brings prices and also a delivery date, classify it as quotation. A dispatch with a tracking number is shipping_notice even when it names an arrival date. Give the reason in one sentence and a confidence between 0 and 1.

Answer only with JSON with the keys kind, confidence and reason.
