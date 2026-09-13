"""The reason behind every approval request, written for the person who decides.

Phase 11 S7: an approval carries ``reasoning`` next to its facts: what the
agent knew (the facts in words), which rule or step led here, how sure it
is, what else the person can do, and the counterfactual: what would have
let the action run alone, or why it never will. Nodes may pass their own
facts and alternatives; this module fills the rest from the request and
the policy's verdict, so no approval leaves without a reason.
"""

from __future__ import annotations

from sc_core.schema.autonomy import NEVER_AUTOMATED, ActionFacts, PolicyDecision, Reasoning

KIND_LABELS: dict[str, str] = {
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


def fact_lines(facts: ActionFacts | None) -> list[str]:
    """The facts as short lines a person reads at a glance; nothing when nothing is known."""
    if facts is None:
        return []
    out: list[str] = []
    if facts.partner_name or facts.partner_id:
        out.append(f"supplier {facts.partner_name or facts.partner_id}")
    if facts.amount is not None:
        out.append(f"amount {facts.amount:,.2f} {facts.currency or ''}".strip())
    if facts.email_kind:
        out.append(f"email kind {facts.email_kind}")
    if facts.change_pct is not None:
        out.append(f"price or quantity move {facts.change_pct:g}%")
    if facts.change_days is not None:
        out.append(f"date move {facts.change_days} day(s)")
    if facts.days_late is not None:
        out.append(f"{facts.days_late} day(s) late")
    if facts.supplier_score is not None:
        out.append(f"supplier score {facts.supplier_score:g}/100")
    if facts.confidence is not None:
        out.append(f"the agent's confidence {round(facts.confidence * 100)}%")
    if facts.first_time_supplier:
        out.append("first order with this supplier")
    return out


def counterfactual_for(kind: str, facts: ActionFacts | None, level: str, forced: bool) -> str:
    """What would have let it run alone, or why it never will."""
    label = KIND_LABELS.get(kind, kind)
    if kind in NEVER_AUTOMATED:
        return f"A {label} is always a person's decision; no autonomy rule can change that."
    if forced:
        return "A person asked to read this one first, so no rule was consulted."
    if level != "approve":
        return "It ran alone under a rule; without that rule a person would have decided."
    hints: list[str] = []
    if facts is not None:
        if facts.partner_id is not None:
            hints.append(f"limited to supplier {facts.partner_name or facts.partner_id}")
        if facts.email_kind:
            hints.append(f"for emails of kind {facts.email_kind}")
        if facts.amount is not None:
            hints.append(f"with an amount cap at or above {facts.amount:,.0f}")
        if facts.confidence is not None:
            hints.append(f"with a confidence floor at or below {round(facts.confidence * 100)}%")
        if facts.change_pct is not None:
            hints.append(f"allowing moves up to {facts.change_pct:g}%")
        if facts.change_days is not None:
            hints.append(f"allowing date moves up to {facts.change_days} day(s)")
    where = (", ".join(hints) + ", ") if hints else ""
    return (
        f"An autonomy rule for {label}s {where}at level auto_notice would have let it run "
        "alone, with a revert window."
    )


def build_reasoning(
    *,
    kind: str,
    facts: ActionFacts | None,
    given: Reasoning | None,
    verdict: PolicyDecision | None,
    level: str,
    forced: bool,
) -> Reasoning:
    """The reasoning stored with an approval: the node's own lines first, the rest filled in."""
    base = given or Reasoning()
    lines = list(base.facts)
    for line in fact_lines(facts):
        if line not in lines:
            lines.append(line)
    if base.rule:
        rule = base.rule
    elif kind in NEVER_AUTOMATED:
        rule = "always a person's decision"
    elif forced:
        rule = "a person asked to read this first"
    elif verdict is not None and verdict.rule_id:
        rule = (
            f"rule {verdict.rule_id}: {verdict.reason}"
            if verdict.reason
            else f"rule {verdict.rule_id}"
        )
    elif verdict is not None:
        rule = verdict.reason or "no autonomy rule matched; a person decides"
    else:
        rule = "no autonomy policy in force; a person decides"
    confidence = (
        base.confidence if base.confidence is not None else (facts.confidence if facts else None)
    )
    alternatives = list(base.alternatives) or list(DEFAULT_ALTERNATIVES.get(kind, []))
    counterfactual = base.counterfactual or counterfactual_for(kind, facts, level, forced)
    return Reasoning(
        facts=lines[:12],
        rule=rule[:300],
        confidence=confidence,
        alternatives=alternatives[:5],
        counterfactual=counterfactual[:400],
    )


__all__ = ["DEFAULT_ALTERNATIVES", "build_reasoning", "counterfactual_for", "fact_lines"]
