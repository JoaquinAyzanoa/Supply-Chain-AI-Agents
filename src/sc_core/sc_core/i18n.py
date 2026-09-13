"""Language of what people read.

Everything internal (prompts, reasoning, tool calls, logs, case events,
structured fields) is English. Only text that reaches a person follows a
language: emails to suppliers (their Odoo language), and explanations,
summaries, approval titles, chatter notes and To-Dos (the instance's
``SC__AGENTS__LANGUAGE``). ``t`` renders those strings from one catalog;
``language_name`` gives prompts the instruction "write it in Spanish".
"""

from __future__ import annotations

from typing import Any, Literal

Language = Literal["en", "es"]
DEFAULT: Language = "en"
SUPPORTED: tuple[Language, ...] = ("en", "es")

LANGUAGE_NAMES: dict[Language, str] = {"en": "English", "es": "Spanish"}


def normalize(code: str | None, default: Language = DEFAULT) -> Language:
    """``es_PE`` → ``es``; anything unsupported → ``default``."""
    if not code:
        return default
    base = code.replace("-", "_").split("_")[0].lower()
    return base if base in SUPPORTED else default  # type: ignore[return-value]


def language_name(lang: Language) -> str:
    return LANGUAGE_NAMES[lang]


MESSAGES: dict[str, dict[Language, str]] = {
    # --- shared ----------------------------------------------------------------------
    "signature": {"en": "Purchasing Team", "es": "Equipo de Compras"},
    # --- logistics: shipping notices -------------------------------------------------
    "shipment.summary": {
        "en": (
            "arrival {date} on {n} line(s) per the shipping notice "
            "(carrier {carrier}, tracking {tracking})"
        ),
        "es": (
            "llegada {date} en {n} línea(s) según el aviso de despacho "
            "(transportista {carrier}, guía {tracking})"
        ),
    },
    "shipment.low_confidence": {
        "en": "arrival date inferred with low confidence",
        "es": "fecha de llegada deducida con baja confianza",
    },
    "shipment.no_date": {
        "en": "shipping notice on {po} recorded; it gives no arrival date",
        "es": "aviso de despacho de {po} registrado; no indica fecha de llegada",
    },
    "shipment.unchanged": {
        "en": "shipping notice on {po} confirms the planned arrival {date}",
        "es": "el aviso de despacho de {po} confirma la llegada prevista {date}",
    },
    "shipment.carrier": {"en": "Carrier", "es": "Transportista"},
    "shipment.tracking": {"en": "Tracking", "es": "Guía"},
    "shipment.shipped": {"en": "Dispatched", "es": "Despachado"},
    "shipment.arrival": {"en": "Arrival", "es": "Llegada"},
    "shipment.partial": {"en": "Partial shipment", "es": "Envío parcial"},
    "shipment.applied_note": {
        "en": "<p>Shipping notice applied: {n} date(s) updated, approved by {who}.</p>",
        "es": "<p>Aviso de despacho aplicado: {n} fecha(s) actualizada(s), aprobado por {who}.</p>",
    },
    "shipment.applied_summary": {
        "en": "arrival date from the shipping notice applied on {n} line(s) of {po}",
        "es": "fecha de llegada del aviso de despacho aplicada en {n} línea(s) de {po}",
    },
    # --- supplier performance -------------------------------------------------------------
    "score.nobody": {
        "en": "no supplier had a confirmed order since {since}; nothing to score",
        "es": "ningún proveedor tuvo órdenes confirmadas desde {since}; nada que puntuar",
    },
    "score.approval_summary": {
        "en": (
            "Weekly supplier scorecards to {end}: {n} supplier(s), {flagged} with changes to watch"
        ),
        "es": (
            "Puntuaciones semanales de proveedores al {end}: {n} proveedor(es), "
            "{flagged} con cambios a vigilar"
        ),
    },
    "score.applied_summary": {
        "en": "scores written on {partners} supplier(s), {entries} price list lead time(s) and "
        "{params} planning parameter(s)",
        "es": "puntuaciones escritas en {partners} proveedor(es), {entries} plazo(s) de la "
        "lista de precios y {params} parámetro(s) de planificación",
    },
    "score.rejected_summary": {
        "en": "weekly scorecards not applied: rejected by {who} ({reason})",
        "es": "puntuaciones semanales no aplicadas: rechazadas por {who} ({reason})",
    },
    # --- invoice matching ---------------------------------------------------------------
    "bill.already": {
        "en": "invoice {number} is already recorded as {bill}",
        "es": "la factura {number} ya está registrada como {bill}",
    },
    "bill.no_order": {
        "en": "the invoice could not be matched to an order: {why}",
        "es": "la factura no pudo asociarse a una orden: {why}",
    },
    "bill.no_supplier": {
        "en": "the invoice names no order and its sender is not a known supplier",
        "es": "la factura no indica orden y su remitente no es un proveedor conocido",
    },
    "bill.approval_clean": {
        "en": "Record invoice {number} from {partner} for {po} ({amount}): it matches",
        "es": "Registrar la factura {number} de {partner} por {po} ({amount}): coincide",
    },
    "bill.approval_hold": {
        "en": "Invoice {number} from {partner} for {po} ({amount}) does not match: decide",
        "es": "La factura {number} de {partner} por {po} ({amount}) no coincide: decidir",
    },
    "bill.auto_reason": {
        "en": "clean invoice under the automatic limit of {amount}",
        "es": "factura sin diferencias bajo el límite automático de {amount}",
    },
    "bill.col.product": {"en": "Product", "es": "Producto"},
    "bill.col.billed": {"en": "Billed", "es": "Facturado"},
    "bill.col.price": {"en": "Unit price", "es": "Precio unitario"},
    "bill.col.ordered": {"en": "Ordered @ price", "es": "Pedido @ precio"},
    "bill.col.received": {"en": "Received", "es": "Recibido"},
    "bill.col.status": {"en": "Check", "es": "Verificación"},
    "bill.status.ok": {"en": "ok", "es": "ok"},
    "bill.status.price_variance": {"en": "price differs", "es": "precio distinto"},
    "bill.status.qty_variance": {"en": "more than ordered", "es": "más de lo pedido"},
    "bill.status.not_received": {"en": "not received", "es": "no recibido"},
    "bill.status.unmatched": {"en": "not on the order", "es": "no está en la orden"},
    "bill.verdict.clean": {"en": "matches", "es": "coincide"},
    "bill.verdict.hold": {"en": "does not match", "es": "no coincide"},
    "bill.check_clean": {
        "en": "All {n} line(s) match the order and the receipts.",
        "es": "Las {n} línea(s) coinciden con la orden y las recepciones.",
    },
    "bill.check_hold": {"en": "Held: {reasons}.", "es": "Retenida: {reasons}."},
    "bill.checked_note": {
        "en": "<p>Vendor bill {bill} checked against the order: {verdict}. Decided by {who}.</p>",
        "es": "<p>Factura {bill} verificada contra la orden: {verdict}. Decidido por {who}.</p>",
    },
    "bill.checked_summary": {
        "en": "bill {bill} checked against {po}",
        "es": "factura {bill} verificada contra {po}",
    },
    "bill.created_note": {
        "en": "<p>Invoice {number} recorded as draft bill {bill} ({verdict}), approved by {who}. "
        "Nothing is posted: accounting posts it.</p>",
        "es": (
            "<p>Factura {number} registrada como borrador {bill} ({verdict}), aprobada por {who}. "
            "No se contabiliza: contabilidad la valida.</p>"
        ),
    },
    "bill.created_summary": {
        "en": "invoice {number} recorded as draft bill {bill} on {po}",
        "es": "factura {number} registrada como borrador {bill} en {po}",
    },
    "bill.rejected_note": {
        "en": "<p>Invoice {number} not recorded: rejected by {who}. Reason: {reason}</p>",
        "es": "<p>Factura {number} no registrada: rechazada por {who}. Motivo: {reason}</p>",
    },
    "bill.rejected_summary": {
        "en": "invoice {number} not recorded: rejected by {who} ({reason})",
        "es": "factura {number} no registrada: rechazada por {who} ({reason})",
    },
    # --- logistics: receipts ------------------------------------------------------------
    "receipt.match_note": {
        "en": "<p>Receipt {picking} checked: {n} line(s) received as ordered.</p>",
        "es": "<p>Recepción {picking} verificada: {n} línea(s) recibidas según lo pedido.</p>",
    },
    "receipt.match_summary": {
        "en": "receipt {picking} matches {po}: {n} line(s) in full",
        "es": "la recepción {picking} coincide con {po}: {n} línea(s) completas",
    },
    "receipt.col.product": {"en": "Product", "es": "Producto"},
    "receipt.col.expected": {"en": "Expected", "es": "Esperado"},
    "receipt.col.received": {"en": "Received", "es": "Recibido"},
    "receipt.col.difference": {"en": "Difference", "es": "Diferencia"},
    "receipt.kind.short": {"en": "short", "es": "faltante"},
    "receipt.kind.over": {"en": "over", "es": "excedente"},
    "receipt.kind.damaged": {"en": "damaged", "es": "dañado"},
    "receipt.approval_summary": {
        "en": "Report receipt discrepancies to {partner} on {po}",
        "es": "Reportar diferencias de recepción a {partner} por {po}",
    },
    "receipt.kind_label": {
        "en": "receipt discrepancy report",
        "es": "reporte de diferencias de recepción",
    },
    "receipt.sent_summary": {
        "en": "receipt discrepancy report sent to {partner} for {po}",
        "es": "reporte de diferencias de recepción enviado a {partner} por {po}",
    },
    "common.no_reason": {"en": "no reason given", "es": "sin motivo"},
    "common.open_outlook": {"en": "Open in Outlook", "es": "Abrir en Outlook"},
    "common.open_control_tower": {
        "en": "Review in the Control Tower",
        "es": "Revisar en la Torre de Control",
    },
    "approval.todo": {"en": "Approval needed: {summary}", "es": "Aprobación pendiente: {summary}"},
    "note.to": {"en": "To", "es": "Para"},
    "note.subject": {"en": "Subject", "es": "Asunto"},
    "note.attachments": {"en": "Attachments", "es": "Adjuntos"},
    "note.product": {"en": "Product", "es": "Producto"},
    "note.change": {"en": "Change", "es": "Cambio"},
    "note.before": {"en": "Before", "es": "Antes"},
    "note.after": {"en": "After", "es": "Después"},
    "note.source": {"en": "Source", "es": "Fuente"},
    "note.needs_review": {"en": "needs review", "es": "requiere revisión"},
    "note.field.date_planned": {"en": "delivery date", "es": "fecha de entrega"},
    "note.field.price": {"en": "price", "es": "precio"},
    "note.field.product_qty": {"en": "quantity", "es": "cantidad"},
    "note.field.lead_time": {"en": "lead time", "es": "plazo de entrega"},
    "note.field.lead_days": {"en": "lead time", "es": "plazo de entrega"},
    "note.plan_totals": {
        "en": "{lines} lines to act on: {rfqs} RFQ lines, {rules} rule changes, "
        "{exceptions} exceptions.",
        "es": "{lines} líneas por actuar: {rfqs} líneas de RFQ, {rules} cambios de regla, "
        "{exceptions} excepciones.",
    },
    "note.exceptions": {"en": "Exceptions", "es": "Excepciones"},
    "note.history": {"en": "What happened so far", "es": "Lo ocurrido hasta ahora"},
    "common.awaiting_approval": {
        "en": "waiting for human approval",
        "es": "esperando aprobación humana",
    },
    # --- supplier communications --------------------------------------------------------
    "kind.rfq": {"en": "request for quotation", "es": "solicitud de cotización"},
    "kind.request_eta": {"en": "delivery date request", "es": "solicitud de fecha de entrega"},
    "kind.follow_up": {"en": "reminder", "es": "recordatorio"},
    "kind.send_po": {"en": "purchase order", "es": "orden de compra"},
    "kind.reply": {"en": "reply", "es": "respuesta"},
    "kind.email": {"en": "email", "es": "correo"},
    "kind.decline": {"en": "decline", "es": "declinación"},
    "kind.answer": {"en": "answer", "es": "respuesta con datos"},
    # --- the morning briefing (phase 11 S7) --------------------------------------------
    "briefing.section.needs_you": {
        "en": "Needs a decision today",
        "es": "Necesita una decisión hoy",
    },
    "briefing.section.risks": {
        "en": "Stock and supplier risks",
        "es": "Riesgos de stock y proveedores",
    },
    "briefing.section.late": {
        "en": "Late and silent orders",
        "es": "Órdenes atrasadas y sin respuesta",
    },
    "briefing.section.overnight": {
        "en": "Since the last briefing",
        "es": "Desde el último informe",
    },
    "briefing.section.ran_alone": {
        "en": "Ran alone under the rules",
        "es": "Corrió solo bajo las reglas",
    },
    "briefing.section.playbooks": {"en": "Plans in progress", "es": "Planes en curso"},
    "briefing.waiting": {"en": "waiting {n} day(s)", "es": "esperando {n} día(s)"},
    "briefing.risk_product": {
        "en": "{ref}: {odds}% chance of a stockout within 30 days",
        "es": "{ref}: {odds}% de probabilidad de quiebre en 30 días",
    },
    "briefing.risk_late": {"en": "late order {po}", "es": "orden atrasada {po}"},
    "briefing.risk_supplier": {
        "en": "{name}: {n} overdue line(s)",
        "es": "{name}: {n} línea(s) vencida(s)",
    },
    "briefing.late_po": {
        "en": "{po}: {days} day(s) past its date without a receipt",
        "es": "{po}: {days} día(s) después de su fecha sin recepción",
    },
    "briefing.silent_rfq": {
        "en": "{po}: no reply to the quotation request for {days} day(s)",
        "es": "{po}: sin respuesta a la cotización hace {days} día(s)",
    },
    "briefing.status.done": {"en": "done", "es": "cerrado"},
    "briefing.status.awaiting_approval": {"en": "waiting for you", "es": "esperando decisión"},
    "briefing.status.escalated": {"en": "in a person's hands", "es": "en manos de una persona"},
    "briefing.status.failed": {"en": "failed", "es": "falló"},
    "briefing.under_rule": {"en": "rule {rule}", "es": "regla {rule}"},
    "briefing.revertible": {"en": "still revertible", "es": "aún reversible"},
    "briefing.playbook": {
        "en": "{title}: {n} active ({where})",
        "es": "{title}: {n} activo(s) ({where})",
    },
    "briefing.email_title": {
        "en": "Purchasing briefing for {day}",
        "es": "Informe de compras del {day}",
    },
    "briefing.nothing": {"en": "nothing", "es": "nada"},
    "briefing.open": {
        "en": "Open the briefing in the Control Tower",
        "es": "Abrir el informe en la Control Tower",
    },
    "briefing.kind.send_email": {"en": "email", "es": "correo"},
    "briefing.kind.po_change": {"en": "order change", "es": "cambio de orden"},
    "briefing.kind.orderpoint_change": {"en": "rule change", "es": "cambio de regla"},
    "briefing.kind.planning_run": {"en": "planning run", "es": "corrida de planificación"},
    "briefing.kind.unlinked_mail": {"en": "unlinked email", "es": "correo sin orden"},
    "briefing.kind.escalation": {"en": "escalation", "es": "escalación"},
    "briefing.kind.vendor_bill": {"en": "vendor bill", "es": "factura"},
    "briefing.kind.supplier_score": {"en": "scorecards", "es": "calificaciones"},
    "briefing.kind.autonomy_change": {"en": "autonomy change", "es": "cambio de autonomía"},
    "briefing.kind.award": {"en": "award", "es": "adjudicación"},
    "briefing.kind.negotiation_offer": {"en": "counter-offer", "es": "contraoferta"},
    "briefing.kind.partner_create": {"en": "new supplier", "es": "proveedor nuevo"},
    "briefing.kind.internal_request": {"en": "internal request", "es": "pedido interno"},
    "briefing.kind.price_list_update": {"en": "price list", "es": "lista de precios"},
    "kind.ack": {"en": "acknowledgement", "es": "acuse de recibo"},
    "kind.status": {"en": "status update", "es": "aviso de estado"},
    # --- disputes, internal requests and price lists (phase 11 S6) ---------------------
    "dispute.summary": {
        "en": "{partner} raises a dispute on {po}: {reason}",
        "es": "{partner} plantea un reclamo sobre {po}: {reason}",
    },
    "dispute.escalated": {
        "en": "dispute on {po} handed to a person: {reason}",
        "es": "reclamo sobre {po} pasado a una persona: {reason}",
    },
    "request.approval_summary": {
        "en": "Internal request from {sender}: {n} item(s){date}",
        "es": "Pedido interno de {sender}: {n} ítem(s){date}",
    },
    "request.by_date": {"en": ", needed by {date}", "es": ", para el {date}"},
    "request.unreadable": {
        "en": "the email from {sender} asks for nothing purchasing can order; a person reads it",
        "es": "el correo de {sender} no pide nada que compras pueda pedir; lo lee una persona",
    },
    "request.nothing_to_order": {
        "en": "no accepted item has a product and a supplier; nothing to order",
        "es": "ningún ítem aceptado tiene producto y proveedor; nada que pedir",
    },
    "request.rfq_origin": {
        "en": "internal request from {sender}",
        "es": "pedido interno de {sender}",
    },
    "request.rfq_note": {
        "en": (
            "<p>RFQ created from the internal request of {sender}: {n} line(s); "
            "approved by {who}.</p>"
        ),
        "es": (
            "<p>RFQ creada desde el pedido interno de {sender}: {n} línea(s); "
            "aprobado por {who}.</p>"
        ),
    },
    "request.ack_intro": {
        "en": "We received your request and asked our suppliers for:",
        "es": "Recibimos tu pedido y solicitamos a nuestros proveedores:",
    },
    "request.ack_rfqs": {
        "en": (
            "Requests for quotation: {rfqs}. We will write again when the order is confirmed "
            "and when the goods arrive."
        ),
        "es": (
            "Solicitudes de cotización: {rfqs}. Te escribiremos cuando la orden esté "
            "confirmada y cuando llegue la mercadería."
        ),
    },
    "request.ack_date": {"en": "Requested for: {date}.", "es": "Pedido para: {date}."},
    "request.ack_unmatched": {
        "en": "Not in our catalogue, a buyer will check: {items}.",
        "es": "No están en nuestro catálogo, un comprador lo revisará: {items}.",
    },
    "request.no_supplier": {
        "en": "; no supplier lists: {items}",
        "es": "; ningún proveedor ofrece: {items}",
    },
    "request.applied_summary": {
        "en": "internal request from {sender}: {n} RFQ(s) created ({rfqs}) and the requester told",
        "es": "pedido interno de {sender}: {n} RFQ(s) creada(s) ({rfqs}) y el solicitante avisado",
    },
    "request.rejected_html": {
        "en": "Your purchase request was not approved by {who}: {reason}",
        "es": "Tu pedido de compra no fue aprobado por {who}: {reason}",
    },
    "request.rejected_summary": {
        "en": "internal request rejected by {who} ({reason}); the requester was told",
        "es": "pedido interno rechazado por {who} ({reason}); se avisó al solicitante",
    },
    "request.not_found": {
        "en": "no internal request behind {po}",
        "es": "no hay pedido interno detrás de {po}",
    },
    "request.status_confirmed": {
        "en": (
            "Your request is on its way: order {po} with {partner} is confirmed, "
            "expected on {date}."
        ),
        "es": (
            "Tu pedido está en camino: la orden {po} con {partner} está confirmada, "
            "prevista para el {date}."
        ),
    },
    "request.status_received": {
        "en": "The goods of order {po} ({partner}) have arrived at the warehouse.",
        "es": "La mercadería de la orden {po} ({partner}) llegó al almacén.",
    },
    "request.status_cancelled": {
        "en": "Order {po} with {partner} was cancelled; purchasing will contact you.",
        "es": "La orden {po} con {partner} fue cancelada; compras te contactará.",
    },
    "request.status_open": {
        "en": "Order {po} is still a request for quotation with {partner}.",
        "es": "La orden {po} sigue siendo una solicitud de cotización con {partner}.",
    },
    "request.status_summary": {
        "en": "status update ({milestone}) sent to the requester of {po}",
        "es": "aviso de estado ({milestone}) enviado al solicitante de {po}",
    },
    "pricelist.approval_summary": {
        "en": "Price list from {partner}: {n} price(s) change, {unmatched} not in the catalogue",
        "es": (
            "Lista de precios de {partner}: {n} precio(s) cambian, {unmatched} fuera del catálogo"
        ),
    },
    "pricelist.applied_summary": {
        "en": "{n} price(s) from {partner}'s list recorded",
        "es": "{n} precio(s) de la lista de {partner} registrados",
    },
    "pricelist.skipped": {"en": " ({n} left out)", "es": " ({n} dejados fuera)"},
    "pricelist.rejected_summary": {
        "en": "price list not applied: rejected by {who} ({reason})",
        "es": "lista de precios no aplicada: rechazada por {who} ({reason})",
    },
    "pricelist.note": {
        "en": "<p>Price list {source} from {partner}: {n} price(s) recorded by {who}.</p>",
        "es": "<p>Lista de precios {source} de {partner}: {n} precio(s) registrados por {who}.</p>",
    },
    "partner.approval_summary": {
        "en": "New supplier? {sender} quoted {n} line(s) without an order of ours",
        "es": "¿Proveedor nuevo? {sender} cotizó {n} línea(s) sin una orden nuestra",
    },
    "partner.rfq_origin": {"en": "quote from {sender}", "es": "cotización de {sender}"},
    "partner.rfq_note": {
        "en": (
            "<p>RFQ created from the quotation {partner} sent: {n} line(s) at the quoted "
            "prices.</p>"
        ),
        "es": (
            "<p>RFQ creada desde la cotización que envió {partner}: {n} línea(s) a los "
            "precios cotizados.</p>"
        ),
    },
    "partner.created_summary": {
        "en": (
            "supplier {partner} created; RFQ {po} with {n} quoted line(s); not matched: {unmatched}"
        ),
        "es": (
            "proveedor {partner} creado; RFQ {po} con {n} línea(s) cotizada(s); sin "
            "coincidencia: {unmatched}"
        ),
    },
    "partner.rejected_summary": {
        "en": "supplier not created: rejected by {who} ({reason})",
        "es": "proveedor no creado: rechazado por {who} ({reason})",
    },
    "kind.counter_offer": {"en": "counter-offer", "es": "contraoferta"},
    # --- sourcing ---------------------------------------------------------------------
    "sourcing.nobody": {
        "en": "no supplier to invite for {product}: nobody lists it and no partner was named",
        "es": "ningún proveedor que invitar para {product}: nadie lo lista y no se indicó socio",
    },
    "sourcing.round_started": {
        "en": "quote round #{round_id} started: {n} supplier(s) invited for {products}",
        "es": (
            "ronda de cotización #{round_id} iniciada: {n} proveedor(es) invitado(s) para "
            "{products}"
        ),
    },
    "sourcing.award_summary": {
        "en": (
            "Award quote round #{round_id} ({products}): {recommended} recommended, "
            "{replied} of {invited} replied"
        ),
        "es": (
            "Adjudicar la ronda #{round_id} ({products}): se recomienda {recommended}, "
            "respondieron {replied} de {invited}"
        ),
    },
    "sourcing.alternate_summary": {
        "en": (
            "Alternative source for {po}: {recommended} can supply {products} from the price list"
        ),
        "es": (
            "Fuente alternativa para {po}: {recommended} puede suministrar {products} según "
            "lista de precios"
        ),
    },
    "sourcing.awarded": {
        "en": "round #{round_id} awarded: {partner}; {declined} other quote(s) declined",
        "es": (
            "ronda #{round_id} adjudicada: {partner}; {declined} otra(s) cotización(es) "
            "declinada(s)"
        ),
    },
    "sourcing.award_rejected": {
        "en": "round #{round_id} not awarded: rejected by {who} ({reason})",
        "es": "ronda #{round_id} sin adjudicar: rechazada por {who} ({reason})",
    },
    "sourcing.nothing_to_compare": {
        "en": "round #{round_id} has no quote and no list price to compare",
        "es": "la ronda #{round_id} no tiene cotizaciones ni precios de lista que comparar",
    },
    "sourcing.offer_summary": {
        "en": (
            "Counter-offer to {partner} on {po}: {product} at {offered} instead of "
            "{current} (round {n} of {max})"
        ),
        "es": (
            "Contraoferta a {partner} en {po}: {product} a {offered} en vez de {current} "
            "(ronda {n} de {max})"
        ),
    },
    "sourcing.offer_sent": {
        "en": "counter-offer of {offered} sent to {partner} for {product} on {po}",
        "es": "contraoferta de {offered} enviada a {partner} por {product} en {po}",
    },
    "sourcing.offer_rejected": {
        "en": "counter-offer not sent: rejected by {who} ({reason})",
        "es": "contraoferta no enviada: rechazada por {who} ({reason})",
    },
    "sourcing.at_target": {
        "en": (
            "{product} on {po} is already at or under the target of {target}; nothing to negotiate"
        ),
        "es": "{product} en {po} ya está en o bajo el objetivo de {target}; nada que negociar",
    },
    "sourcing.rounds_exhausted": {
        "en": "{po}: {n} negotiation round(s) already made; a person decides",
        "es": "{po}: ya se hicieron {n} ronda(s) de negociación; decide una persona",
    },
    "sourcing.no_alternate": {
        "en": (
            "no alternative supplier lists the products of {po}; a quote round would start "
            "from zero"
        ),
        "es": (
            "ningún proveedor alternativo lista los productos de {po}; una ronda partiría de cero"
        ),
    },
    "sourcing.decline_notes": {
        "en": (
            "Our request for quotation went to another supplier this time. Thank them for "
            "the quote, keep the door open for future needs, and do not give the winning "
            "price."
        ),
        "es": (
            "Esta vez la solicitud fue adjudicada a otro proveedor. Agradecer la "
            "cotización, dejar la puerta abierta para futuras necesidades y no revelar el "
            "precio ganador."
        ),
    },
    "sourcing.counter_notes": {
        "en": (
            "Counter-offer: for {product} (qty {qty}) we ask {offered} {currency} per unit "
            "instead of the quoted {current}. Basis: {basis}. Ask for confirmation of the "
            "new price and keep the delivery terms."
        ),
        "es": (
            "Contraoferta: para {product} (cant. {qty}) pedimos {offered} {currency} por "
            "unidad en vez de los {current} cotizados. Base: {basis}. Pedir confirmación "
            "del nuevo precio manteniendo las condiciones de entrega."
        ),
    },
    "send.approval_summary": {
        "en": "Send {label} to {partner} for {po}",
        "es": "Enviar {label} a {partner} por {po}",
    },
    "send.auto_reason": {
        "en": "supplier is on the auto-send list",
        "es": "proveedor en la lista de envío automático",
    },
    "send.auto_reason_kind": {
        "en": "{label} emails go out without approval",
        "es": "los correos de {label} salen sin aprobación",
    },
    "send.note": {
        "en": "<p>{label} sent to {to}.<br/>Subject: {subject}.{link}</p>",
        "es": "<p>{label} enviada a {to}.<br/>Asunto: {subject}.{link}</p>",
    },
    "send.summary": {
        "en": "{label} sent to {partner} for {po}",
        "es": "{label} enviada a {partner} por {po}",
    },
    "send.rejected_note": {
        "en": "<p>Sending rejected by {who}: {reason}. The draft stays in Outlook.</p>",
        "es": "<p>Envío rechazado por {who}: {reason}. El borrador sigue en Outlook.</p>",
    },
    "send.rejected_summary": {
        "en": "sending rejected by {who}: {reason}",
        "es": "envío rechazado por {who}: {reason}",
    },
    "changes.approval_summary": {
        "en": "Proposed changes on {po}: {summary}",
        "es": "Cambios propuestos en {po}: {summary}",
    },
    "changes.applied_note": {
        "en": "<p>{n} change(s) approved by {who} applied to the order:</p>",
        "es": "<p>{n} cambio(s) aprobado(s) por {who} aplicado(s) a la orden:</p>",
    },
    "changes.pending_note": {
        "en": "<p>Pending manual review:</p>",
        "es": "<p>Pendientes de revisión manual:</p>",
    },
    "changes.applied_summary": {
        "en": "{n} change(s) applied on {po}",
        "es": "{n} cambio(s) aplicado(s) en {po}",
    },
    "changes.pending_summary": {
        "en": ", {n} pending manual review",
        "es": ", {n} pendiente(s) de revisión",
    },
    "changes.rejected_note": {
        "en": "<p>Proposed changes rejected by {who}: {reason}.</p>",
        "es": "<p>Cambios propuestos rechazados por {who}: {reason}.</p>",
    },
    "changes.rejected_summary": {
        "en": "changes rejected by {who}: {reason}",
        "es": "cambios rechazados por {who}: {reason}",
    },
    "changes.no_action": {
        "en": "{po}: email classified as {kind}; {reason}",
        "es": "{po}: correo clasificado como {kind}; {reason}",
    },
    "changes.none": {"en": "no changes versus Odoo", "es": "sin cambios respecto a Odoo"},
    "changes.dates": {
        "en": "delivery date {date} on {n} line(s)",
        "es": "fecha de entrega {date} en {n} línea(s)",
    },
    "changes.prices": {"en": "{n} price(s)", "es": "{n} precio(s)"},
    "changes.splits": {"en": "{n} split delivery(ies)", "es": "{n} entrega(s) parcial(es)"},
    "changes.low_date": {
        "en": "date read with low confidence",
        "es": "fecha interpretada con baja confianza",
    },
    "changes.split_mismatch": {
        "en": "the parts add up to {parts}, the line orders {qty}",
        "es": "las partes suman {parts}, la línea pide {qty}",
    },
    "changes.leads": {"en": "{n} lead time(s)", "es": "{n} plazo(s)"},
    "changes.to_review": {"en": "; {n} to review", "es": "; {n} para revisar"},
    "changes.supplier_wrote": {"en": ' (supplier: "{raw}")', "es": ' (proveedor: "{raw}")'},
    "changes.unmatched": {
        "en": "product not matched to an order line",
        "es": "producto no emparejado con una línea de la orden",
    },
    "changes.review_label": {"en": "review", "es": "revisar"},
    "resolution.summary": {
        "en": "message from an unknown sender with no open orders to match",
        "es": "mensaje de un remitente desconocido sin órdenes abiertas que coincidan",
    },
    # --- director ------------------------------------------------------------------------
    "escalation.todo": {"en": "Escalation {name}", "es": "Escalación {name}"},
    "escalation.reason": {"en": "Reason: {reason}", "es": "Motivo: {reason}"},
    "escalation.trace": {"en": "Trace in Langfuse", "es": "Traza en Langfuse"},
    "approval.reminder": {
        "en": '<p>Reminder: "{summary}" has been waiting for a decision for {days} days.</p>',
        "es": '<p>Recordatorio: "{summary}" lleva {days} días esperando una decisión.</p>',
    },
    "approval.expired": {
        "en": "no answer from the approver in {days} days",
        "es": "sin respuesta del aprobador en {days} días",
    },
    # --- inventory planning ---------------------------------------------------------------
    "plan.approval_summary": {
        "en": "Replenishment plan {date}: {rfqs} RFQs, {rules} rules, {exceptions} exceptions",
        "es": (
            "Plan de reposición {date}: {rfqs} cotizaciones, {rules} "
            "reglas, {exceptions} excepciones"
        ),
    },
    "plan.awaiting": {
        "en": "waiting for the plan's approval",
        "es": "esperando aprobación del plan",
    },
    "plan.applied": {
        "en": "{rules} rules written, {needs} need(s) sent to sourcing",
        "es": "{rules} reglas escritas, {needs} necesidad(es) enviada(s) a abastecimiento",
    },
    "plan.existing": {"en": ", {n} already existed", "es": ", {n} ya existían"},
    "plan.rejected": {"en": "plan rejected: {reason}", "es": "plan rechazado: {reason}"},
    "plan.rejected_default": {"en": "rejected by the approver", "es": "rechazado por el aprobador"},
    "plan.simulation": {"en": "simulation: {summary}", "es": "simulación: {summary}"},
    "plan.nothing": {"en": "nothing to apply", "es": "nada que aplicar"},
    "plan.on_hold": {"en": "on hold: {refs}", "es": "en espera: {refs}"},
    "plan.manual_review": {"en": "manual review: {refs}", "es": "revisión manual: {refs}"},
    "plan.review_note": {"en": "Review: {reason}", "es": "Revisión: {reason}"},
    "plan.review_unavailable": {
        "en": "no model review: {error}",
        "es": "sin revisión del modelo: {error}",
    },
    "plan.explanation_unavailable": {
        "en": "{exception}: no explanation ({error})",
        "es": "{exception}: sin explicación ({error})",
    },
    "plan.action_label": {"en": "Action:", "es": "Acción:"},
    "plan.what_if_never_writes": {
        "en": "what_if runs never write",
        "es": "las simulaciones nunca escriben",
    },
}


def t(key: str, lang: Language | str | None, **values: Any) -> str:
    """The message ``key`` in ``lang`` (English when the key has no translation)."""
    entry = MESSAGES[key]
    chosen = normalize(lang) if lang else DEFAULT
    text = entry.get(chosen) or entry[DEFAULT]
    return text.format(**values) if values else text
