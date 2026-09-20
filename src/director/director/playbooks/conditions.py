"""The conditions a playbook step may test, in code.

Each condition reads the order's facts as the follow-up job gathers them
(``PoFacts``: state, receipt, last email out and in) and today's date.
Nothing here asks a model; a playbook only branches on what Odoo and our
own tables say.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from director.policies import PoFacts

Condition = Callable[[PoFacts, date], bool]


def always(_: PoFacts, __: date) -> bool:
    return True


def no_reply(facts: PoFacts, today: date) -> bool:
    """We wrote and the supplier has not answered since."""
    return facts.silent_days(today) is not None


def reply_received(facts: PoFacts, today: date) -> bool:
    return facts.last_inbound_at is not None and not no_reply(facts, today)


def received(facts: PoFacts, _: date) -> bool:
    return facts.receipt_status == "full" or facts.state == "done"


def not_received(facts: PoFacts, today: date) -> bool:
    return not received(facts, today)


def late(facts: PoFacts, today: date) -> bool:
    return facts.is_confirmed_open and facts.date_planned is not None and facts.date_planned < today


def confirmed(facts: PoFacts, _: date) -> bool:
    return facts.state in ("purchase", "done")


def still_rfq(facts: PoFacts, _: date) -> bool:
    return facts.is_rfq


def unsent_rfq(facts: PoFacts, _: date) -> bool:
    """A quotation request nobody has emailed yet (Odoo keeps it in draft)."""
    return facts.state == "draft"


def awaiting_human(facts: PoFacts, _: date) -> bool:
    return facts.awaiting_human


CONDITIONS: dict[str, Condition] = {
    "always": always,
    "no_reply": no_reply,
    "reply_received": reply_received,
    "received": received,
    "not_received": not_received,
    "late": late,
    "confirmed": confirmed,
    "still_rfq": still_rfq,
    "unsent_rfq": unsent_rfq,
    "awaiting_human": awaiting_human,
}


def holds(name: str, facts: PoFacts, today: date) -> bool:
    return CONDITIONS[name](facts, today)
