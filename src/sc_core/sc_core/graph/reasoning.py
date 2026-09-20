"""The reason behind every approval request, written for the person who decides.

Phase 11 S7: an approval carries ``reasoning`` next to its facts: what the
agent knew (the facts in words), which rule or step led here, how sure it
is, what else the person can do, and the counterfactual: what would have
let the action run alone, or why it never will. Nodes may pass their own
facts and alternatives; this module fills the rest from the request and
the policy's verdict, so no approval leaves without a reason.

A person reads it, so it follows the instance's language (``SC__AGENTS__LANGUAGE``).
The policy's own verdict stays English (it is also an audit value); the two verdicts
a person meets here are translated by ``POLICY_REASONS``.
"""

from __future__ import annotations

from sc_core.i18n import Language
from sc_core.schema.autonomy import NEVER_AUTOMATED, ActionFacts, PolicyDecision, Reasoning

KIND_LABELS: dict[Language, dict[str, str]] = {
    "en": {
        "send_email": "email",
        "po_change": "order change",
        "orderpoint_change": "reorder rule change",
        "planning_run": "planning run",
        "unlinked_mail": "unlinked email",
        "escalation": "escalation",
        "vendor_bill": "vendor bill",
        "supplier_score": "supplier scorecards",
        "autonomy_change": "autonomy change",
        "award": "award",
        "negotiation_offer": "counter-offer",
        "partner_create": "new supplier",
        "internal_request": "internal request",
        "price_list_update": "price list update",
    },
    "es": {
        "send_email": "correo",
        "po_change": "cambio de orden",
        "orderpoint_change": "cambio de regla de reposición",
        "planning_run": "plan de compras",
        "unlinked_mail": "correo sin vincular",
        "escalation": "escalamiento",
        "vendor_bill": "factura de proveedor",
        "supplier_score": "evaluación de proveedores",
        "autonomy_change": "cambio de autonomía",
        "award": "adjudicación",
        "negotiation_offer": "contraoferta",
        "partner_create": "proveedor nuevo",
        "internal_request": "pedido interno",
        "price_list_update": "actualización de lista de precios",
    },
}

DEFAULT_ALTERNATIVES: dict[str, list[str]] = {
    "send_email": [
        "Edit the text before it goes out",
        "Reject it and write to the supplier yourself",
    ],
    "po_change": ["Accept only some of the lines", "Reject it and ask the supplier again"],
    "orderpoint_change": ["Edit the quantities before applying", "Keep the current rule"],
    "planning_run": ["Untick lines", "Edit quantities or the parameters and simulate first"],
    "vendor_bill": ["Hold the bill and ask the supplier for a credit note"],
    "award": [
        "Give every line to one supplier",
        "Choose a supplier per line",
        "Reject and negotiate",
    ],
    "negotiation_offer": [
        "Change the offered price within the limits",
        "Reject and accept the quote",
    ],
    "partner_create": ["Correct the company name", "Reject: this is not a supplier we want"],
    "internal_request": ["Untick items", "Order everything from one supplier"],
    "price_list_update": ["Record only some rows", "Reject and keep the current prices"],
    "escalation": ["Decide in the case chat: hold, close, or ask the supplier"],
    "unlinked_mail": ["Link the email to an order from the case chat", "Close it as noise"],
    "supplier_score": ["Adjust a score before publishing"],
    "autonomy_change": ["Narrow the rule before approving"],
}

ALTERNATIVES_ES: dict[str, list[str]] = {
    "send_email": [
        "Editar el texto antes de que salga",
        "Rechazarlo y escribirle tú al proveedor",
    ],
    "po_change": ["Aceptar solo algunas líneas", "Rechazarlo y volver a preguntar al proveedor"],
    "orderpoint_change": ["Editar las cantidades antes de aplicar", "Mantener la regla actual"],
    "planning_run": ["Desmarcar líneas", "Editar cantidades o parámetros y simular primero"],
    "vendor_bill": ["Retener la factura y pedir una nota de crédito al proveedor"],
    "award": [
        "Dar todas las líneas a un proveedor",
        "Elegir un proveedor por línea",
        "Rechazar y negociar",
    ],
    "negotiation_offer": [
        "Cambiar el precio ofrecido dentro de los límites",
        "Rechazar y aceptar la cotización",
    ],
    "partner_create": [
        "Corregir el nombre de la empresa",
        "Rechazar: no es un proveedor que queramos",
    ],
    "internal_request": ["Desmarcar ítems", "Pedir todo a un solo proveedor"],
    "price_list_update": ["Registrar solo algunas filas", "Rechazar y mantener los precios"],
    "escalation": ["Decidir en el chat del caso: esperar, cerrar o preguntar al proveedor"],
    "unlinked_mail": [
        "Vincular el correo a una orden desde el chat del caso",
        "Cerrarlo: es ruido",
    ],
    "supplier_score": ["Ajustar un puntaje antes de publicar"],
    "autonomy_change": ["Acotar la regla antes de aprobar"],
}

TEXTS: dict[Language, dict[str, str]] = {
    "en": {
        "fact.supplier": "supplier {name}",
        "fact.amount": "amount {amount}",
        "fact.email_kind": "email kind {kind}",
        "fact.change_pct": "price or quantity move {pct:g}%",
        "fact.change_days": "date move {days} day(s)",
        "fact.days_late": "{days} day(s) late",
        "fact.score": "supplier score {score:g}/100",
        "fact.confidence": "the agent's confidence {pct}%",
        "fact.first_time": "first order with this supplier",
        "never": "A {label} is always a person's decision; no autonomy rule can change that.",
        "forced": "A person asked to read this one first, so no rule was consulted.",
        "ran_alone": "It ran alone under a rule; without that rule a person would have decided.",
        "hint.supplier": "limited to supplier {name}",
        "hint.email_kind": "for emails of kind {kind}",
        "hint.amount": "with an amount cap at or above {amount:,.0f}",
        "hint.confidence": "with a confidence floor at or below {pct}%",
        "hint.change_pct": "allowing moves up to {pct:g}%",
        "hint.change_days": "allowing date moves up to {days} day(s)",
        "would": (
            "An autonomy rule for {label}s {where}at level auto_notice would have let it run "
            "alone, with a revert window."
        ),
        "rule.never": "always a person's decision",
        "rule.forced": "a person asked to read this first",
        "rule.id": "rule {id}",
        "rule.none": "no autonomy rule matched; a person decides",
        "rule.no_policy": "no autonomy policy in force; a person decides",
    },
    "es": {
        "fact.supplier": "proveedor {name}",
        "fact.amount": "importe {amount}",
        "fact.email_kind": "tipo de correo {kind}",
        "fact.change_pct": "cambio de precio o cantidad {pct:g}%",
        "fact.change_days": "cambio de fecha {days} día(s)",
        "fact.days_late": "{days} día(s) de atraso",
        "fact.score": "puntaje del proveedor {score:g}/100",
        "fact.confidence": "confianza del agente {pct}%",
        "fact.first_time": "primera orden con este proveedor",
        "never": (
            "Esta decisión ({label}) siempre es de una persona; ninguna regla de autonomía "
            "puede cambiarlo."
        ),
        "forced": "Una persona pidió leer esto primero, así que no se consultó ninguna regla.",
        "ran_alone": "Corrió solo bajo una regla; sin esa regla lo habría decidido una persona.",
        "hint.supplier": "limitada al proveedor {name}",
        "hint.email_kind": "para correos de tipo {kind}",
        "hint.amount": "con un tope de importe de {amount:,.0f} o más",
        "hint.confidence": "con un piso de confianza de {pct}% o menos",
        "hint.change_pct": "que permita cambios de hasta {pct:g}%",
        "hint.change_days": "que permita cambios de fecha de hasta {days} día(s)",
        "would": (
            "Una regla de autonomía para {label} {where}en el nivel auto_notice lo habría dejado "
            "correr solo, con una ventana para revertir."
        ),
        "rule.never": "siempre lo decide una persona",
        "rule.forced": "una persona pidió leer esto primero",
        "rule.id": "regla {id}",
        "rule.none": "ninguna regla de autonomía aplica; decide una persona",
        "rule.no_policy": "no hay política de autonomía vigente; decide una persona",
    },
}

# the policy's verdicts a person meets in an approval, in the languages people read
POLICY_REASONS: dict[str, dict[Language, str]] = {
    "no rule matched": {"en": "no rule matched", "es": "ninguna regla aplica"},
    "always a person's decision": {
        "en": "always a person's decision",
        "es": "siempre lo decide una persona",
    },
}


def _text(lang: Language, key: str, **values: object) -> str:
    return TEXTS.get(lang, TEXTS["en"])[key].format(**values)


def fact_lines(facts: ActionFacts | None, lang: Language = "en") -> list[str]:
    """The facts as short lines a person reads at a glance; nothing when nothing is known."""
    if facts is None:
        return []
    out: list[str] = []
    if facts.partner_name or facts.partner_id:
        out.append(_text(lang, "fact.supplier", name=facts.partner_name or facts.partner_id))
    if facts.amount is not None:
        amount = f"{facts.amount:,.2f} {facts.currency or ''}".strip()
        out.append(_text(lang, "fact.amount", amount=amount))
    if facts.email_kind:
        out.append(_text(lang, "fact.email_kind", kind=facts.email_kind))
    if facts.change_pct is not None:
        out.append(_text(lang, "fact.change_pct", pct=facts.change_pct))
    if facts.change_days is not None:
        out.append(_text(lang, "fact.change_days", days=facts.change_days))
    if facts.days_late is not None:
        out.append(_text(lang, "fact.days_late", days=facts.days_late))
    if facts.supplier_score is not None:
        out.append(_text(lang, "fact.score", score=facts.supplier_score))
    if facts.confidence is not None:
        out.append(_text(lang, "fact.confidence", pct=round(facts.confidence * 100)))
    if facts.first_time_supplier:
        out.append(_text(lang, "fact.first_time"))
    return out


def counterfactual_for(
    kind: str, facts: ActionFacts | None, level: str, forced: bool, lang: Language = "en"
) -> str:
    """What would have let it run alone, or why it never will."""
    label = KIND_LABELS.get(lang, KIND_LABELS["en"]).get(kind, kind)
    if kind in NEVER_AUTOMATED:
        return _text(lang, "never", label=label)
    if forced:
        return _text(lang, "forced")
    if level != "approve":
        return _text(lang, "ran_alone")
    hints: list[str] = []
    if facts is not None:
        if facts.partner_id is not None:
            name = facts.partner_name or facts.partner_id
            hints.append(_text(lang, "hint.supplier", name=name))
        if facts.email_kind:
            hints.append(_text(lang, "hint.email_kind", kind=facts.email_kind))
        if facts.amount is not None:
            hints.append(_text(lang, "hint.amount", amount=facts.amount))
        if facts.confidence is not None:
            hints.append(_text(lang, "hint.confidence", pct=round(facts.confidence * 100)))
        if facts.change_pct is not None:
            hints.append(_text(lang, "hint.change_pct", pct=facts.change_pct))
        if facts.change_days is not None:
            hints.append(_text(lang, "hint.change_days", days=facts.change_days))
    where = (", ".join(hints) + ", ") if hints else ""
    return _text(lang, "would", label=label, where=where)


def _rule_line(verdict: PolicyDecision, lang: Language) -> str:
    """The policy's verdict in words: a person's note as written, the known verdicts translated."""
    reason = verdict.reason or ""
    if verdict.rule_id:
        named = _text(lang, "rule.id", id=verdict.rule_id)
        # A rule without a note describes itself in English ("rule x: amount ≤ 500"); other
        # languages name the rule and leave its conditions to the Autonomy page.
        if not reason or (lang != "en" and reason.startswith(f"rule {verdict.rule_id}:")):
            return named
        return f"{named}: {reason}"
    if reason in POLICY_REASONS:
        return POLICY_REASONS[reason].get(lang, reason)
    return reason or _text(lang, "rule.none")


def build_reasoning(
    *,
    kind: str,
    facts: ActionFacts | None,
    given: Reasoning | None,
    verdict: PolicyDecision | None,
    level: str,
    forced: bool,
    lang: Language = "en",
) -> Reasoning:
    """The reasoning stored with an approval: the node's own lines first, the rest filled in."""
    base = given or Reasoning()
    lines = list(base.facts)
    for line in fact_lines(facts, lang):
        if line not in lines:
            lines.append(line)
    if base.rule:
        rule = base.rule
    elif kind in NEVER_AUTOMATED:
        rule = _text(lang, "rule.never")
    elif forced:
        rule = _text(lang, "rule.forced")
    elif verdict is not None:
        rule = _rule_line(verdict, lang)
    else:
        rule = _text(lang, "rule.no_policy")
    confidence = (
        base.confidence if base.confidence is not None else (facts.confidence if facts else None)
    )
    defaults = ALTERNATIVES_ES if lang == "es" else DEFAULT_ALTERNATIVES
    alternatives = list(base.alternatives) or list(defaults.get(kind, []))
    counterfactual = base.counterfactual or counterfactual_for(kind, facts, level, forced, lang)
    return Reasoning(
        facts=lines[:12],
        rule=rule[:300],
        confidence=confidence,
        alternatives=alternatives[:5],
        counterfactual=counterfactual[:400],
    )


__all__ = ["DEFAULT_ALTERNATIVES", "build_reasoning", "counterfactual_for", "fact_lines"]
