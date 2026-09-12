"""Follow-up policy: when silence or lateness on a purchase order deserves an email or a person.

The policy is a pure function of facts and a date, so every rule is a row
in a table test. Values come from ``SC__DIRECTOR__*`` and will be editable
in the Control Tower (phase 8).

Rules, evaluated in order for one order (the first that fires wins):

* ``rfq_silent``      an RFQ we emailed got no reply for ``rfq_no_reply_days[n]``
                      days, ``n`` being the follow-ups already sent → ``follow_up``
* ``rfq_unanswered``  every follow-up was sent and the silence went past the
                      last threshold → escalate
* ``po_late``         a confirmed order is ``po_late_days[0]`` days past its
                      planned date without a full receipt → ``request_eta``
* ``po_late_escalate`` ... and ``po_late_days[1]`` days past → escalate
* ``eta_before_due``  a confirmed order is due within
                      ``po_eta_request_before_days`` and we never asked → ``request_eta``

A rule fires once per order (``rules_fired`` holds the keys of rules that
fired on any case of the order; date-based rules carry the planned date in
the key, so a moved date can fire them again), and nothing fires while a
case on the order waits for a human (pending approval or escalated).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from sc_core.infra.settings import DirectorCfg
from sc_core.schema.base import StrictModel

TaskKind = Literal["follow_up", "request_eta"]


class FollowUpPolicy(StrictModel):
    rfq_no_reply_days: list[int] = [3, 7]
    po_eta_request_before_days: int = 5
    po_late_days: list[int] = [1, 4]
    approval_stale_days: int = 2
    approval_expire_days: int = 7
    max_actions_per_run: int = 20

    @classmethod
    def from_settings(cls, cfg: DirectorCfg) -> FollowUpPolicy:
        return cls(
            rfq_no_reply_days=list(cfg.rfq_no_reply_days),
            po_eta_request_before_days=cfg.po_eta_request_before_days,
            po_late_days=list(cfg.po_late_days),
            approval_stale_days=cfg.approval_stale_days,
            approval_expire_days=cfg.approval_expire_days,
            max_actions_per_run=cfg.max_actions_per_run,
        )


class PoFacts(StrictModel):
    """What the job knows about one order, from Odoo and our own tables."""

    po_id: int
    po_name: str
    partner_id: int
    state: str
    date_planned: date | None = None
    receipt_status: str | None = None
    last_outbound_at: date | None = Field(default=None, description="last email we sent about it")
    last_inbound_at: date | None = Field(default=None, description="last supplier message linked")
    rules_fired: list[str] = []
    awaiting_human: bool = False

    @property
    def is_rfq(self) -> bool:
        return self.state in ("draft", "sent", "to approve")

    @property
    def is_confirmed_open(self) -> bool:
        return self.state == "purchase" and self.receipt_status != "full"

    @property
    def followups_sent(self) -> int:
        return sum(1 for r in self.rules_fired if r == "rfq_silent")

    def fired(self, rule: str) -> bool:
        return self.key_for(rule) in self.rules_fired

    def key_for(self, rule: str) -> str:
        """Rule key: date-based rules are per planned date, RFQ rules per order."""
        if rule.startswith("rfq_") or self.date_planned is None:
            return rule
        return f"{rule}:{self.date_planned.isoformat()}"

    def silent_days(self, today: date) -> int | None:
        """Days since our last email with no supplier reply after it; ``None`` if not silent."""
        if self.last_outbound_at is None:
            return None
        if self.last_inbound_at is not None and self.last_inbound_at >= self.last_outbound_at:
            return None
        return (today - self.last_outbound_at).days


class Decision(StrictModel):
    po_name: str
    rule: str
    key: str
    task: TaskKind | None = None
    escalate: bool = False
    days: int = 0
    reason: str


def decide(facts: PoFacts, policy: FollowUpPolicy, today: date) -> Decision | None:
    """The one thing to do for this order today, or nothing."""
    if facts.awaiting_human:
        return None
    if facts.is_rfq:
        return _decide_rfq(facts, policy, today)
    if facts.is_confirmed_open and facts.date_planned is not None:
        return _decide_confirmed(facts, policy, today)
    return None


def _decide_rfq(facts: PoFacts, policy: FollowUpPolicy, today: date) -> Decision | None:
    silent = facts.silent_days(today)
    if silent is None or not policy.rfq_no_reply_days:
        return None
    sent = facts.followups_sent
    thresholds = policy.rfq_no_reply_days
    if sent < len(thresholds):
        if silent >= thresholds[sent]:
            return Decision(
                po_name=facts.po_name,
                rule="rfq_silent",
                key="rfq_silent",
                task="follow_up",
                days=silent,
                reason=f"no reply to the RFQ for {silent} days (follow-up {sent + 1})",
            )
        return None
    if not facts.fired("rfq_unanswered") and silent > thresholds[-1]:
        return Decision(
            po_name=facts.po_name,
            rule="rfq_unanswered",
            key="rfq_unanswered",
            escalate=True,
            days=silent,
            reason=f"no reply to the RFQ after {sent} follow-ups and {silent} days",
        )
    return None


def _decide_confirmed(facts: PoFacts, policy: FollowUpPolicy, today: date) -> Decision | None:
    assert facts.date_planned is not None
    late = (today - facts.date_planned).days
    if late > 0 and len(policy.po_late_days) >= 2:
        if facts.fired("po_late_escalate"):
            return None  # a person has it
        if late >= policy.po_late_days[1]:
            return Decision(
                po_name=facts.po_name,
                rule="po_late_escalate",
                key=facts.key_for("po_late_escalate"),
                escalate=True,
                days=late,
                reason=f"{late} days past the planned date without a receipt or a new ETA",
            )
        if late >= policy.po_late_days[0] and not facts.fired("po_late"):
            return Decision(
                po_name=facts.po_name,
                rule="po_late",
                key=facts.key_for("po_late"),
                task="request_eta",
                days=late,
                reason=f"{late} days past the planned date without a receipt",
            )
        return None
    due_in = -late
    if 0 <= due_in <= policy.po_eta_request_before_days and not facts.fired("eta_before_due"):
        if facts.silent_days(today) is not None:
            return None  # we already wrote and are waiting for the answer
        return Decision(
            po_name=facts.po_name,
            rule="eta_before_due",
            key=facts.key_for("eta_before_due"),
            task="request_eta",
            days=due_in,
            reason=f"due in {due_in} days; asking the supplier to confirm the date",
        )
    return None
