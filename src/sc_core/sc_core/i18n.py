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
        "en": "{rules} rules written, {rfqs} RFQs created",
        "es": "{rules} reglas escritas, {rfqs} solicitudes de cotización creadas",
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
