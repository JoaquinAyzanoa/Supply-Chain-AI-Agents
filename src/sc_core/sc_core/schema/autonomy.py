"""The autonomy policy: which proposed actions need a person, which run alone.

Every approval an agent would request carries ``ActionFacts`` (who the
supplier is, how much money, how confident the agent is, how big the change
is). The policy is an ordered list of rules a person edits in the Control
Tower; the first enabled rule whose kind and conditions match decides the
level, and the default is ``approve``. Levels:

* ``approve``      a person decides, as always;
* ``auto_notice``  applied at once, shown in the "done automatically" feed
                   and revertible for ``revert_hours``;
* ``auto``         applied at once and only logged.

A condition that needs a fact the request does not carry does not match:
a rule can only widen autonomy where the agent can prove the facts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import Field, field_validator

from sc_core.schema.base import StrictModel

AutonomyLevel = Literal["approve", "auto_notice", "auto"]
LEVEL_RANK: dict[str, int] = {"approve": 0, "auto_notice": 1, "auto": 2}

# The approval kinds a rule may name ("*" for any). Kept here, not imported from
# the Odoo models, so the schema package stays free of Odoo.
RULE_KINDS: tuple[str, ...] = (
    "*",
    "send_email",
    "po_change",
    "orderpoint_change",
    "planning_run",
    "unlinked_mail",
    "escalation",
    "vendor_bill",
    "supplier_score",
    "autonomy_change",
)
# Kinds no rule may ever automate: money commitments, relationships, the policy itself.
NEVER_AUTOMATED: frozenset[str] = frozenset({"escalation", "autonomy_change"})


class ActionFacts(StrictModel):
    """What the agent knows about the action it proposes; the rules test these."""

    partner_id: int | None = None
    partner_name: str | None = None
    amount: float | None = Field(default=None, ge=0, description="money involved")
    currency: str | None = None
    supplier_score: float | None = Field(default=None, ge=0, le=100)
    confidence: float | None = Field(default=None, ge=0, le=1, description="the agent's own")
    change_pct: float | None = Field(default=None, ge=0, description="price or quantity move")
    change_days: int | None = Field(default=None, ge=0, description="date move, in days")
    days_late: int | None = Field(default=None, ge=0)
    first_time_supplier: bool = False
    email_kind: str | None = None


class RuleConditions(StrictModel):
    """Every set condition must hold. Lists match any member; empty lists match all."""

    partner_ids: list[int] = []
    email_kinds: list[str] = []
    amount_max: float | None = Field(default=None, ge=0)
    supplier_score_min: float | None = Field(default=None, ge=0, le=100)
    confidence_min: float | None = Field(default=None, ge=0, le=1)
    change_pct_max: float | None = Field(default=None, ge=0)
    change_days_max: int | None = Field(default=None, ge=0)
    days_late_max: int | None = Field(default=None, ge=0)
    first_time_supplier: bool | None = None

    def matches(self, facts: ActionFacts) -> bool:
        checks: list[tuple[Any, Any, str]] = [
            (self.amount_max, facts.amount, "lte"),
            (self.supplier_score_min, facts.supplier_score, "gte"),
            (self.confidence_min, facts.confidence, "gte"),
            (self.change_pct_max, facts.change_pct, "lte"),
            (self.change_days_max, facts.change_days, "lte"),
            (self.days_late_max, facts.days_late, "lte"),
        ]
        for bound, value, op in checks:
            if bound is None:
                continue
            if value is None:
                return False  # the agent could not prove it: no autonomy on a guess
            if op == "lte" and not value <= bound:
                return False
            if op == "gte" and not value >= bound:
                return False
        if self.partner_ids and facts.partner_id not in self.partner_ids:
            return False
        if self.email_kinds and facts.email_kind not in self.email_kinds:
            return False
        if (
            self.first_time_supplier is not None
            and facts.first_time_supplier != self.first_time_supplier
        ):
            return False
        return True

    def describe(self) -> str:
        parts: list[str] = []
        if self.partner_ids:
            parts.append(f"suppliers {', '.join(str(i) for i in self.partner_ids)}")
        if self.email_kinds:
            parts.append(f"emails {', '.join(self.email_kinds)}")
        if self.amount_max is not None:
            parts.append(f"amount ≤ {self.amount_max:g}")
        if self.supplier_score_min is not None:
            parts.append(f"score ≥ {self.supplier_score_min:g}")
        if self.confidence_min is not None:
            parts.append(f"confidence ≥ {self.confidence_min:g}")
        if self.change_pct_max is not None:
            parts.append(f"change ≤ {self.change_pct_max:g} %")
        if self.change_days_max is not None:
            parts.append(f"date move ≤ {self.change_days_max} d")
        if self.days_late_max is not None:
            parts.append(f"late ≤ {self.days_late_max} d")
        if self.first_time_supplier is not None:
            parts.append("new supplier" if self.first_time_supplier else "known supplier")
        return ", ".join(parts) or "always"


class AutonomyRule(StrictModel):
    id: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    kind: str = "*"
    when: RuleConditions = Field(default_factory=RuleConditions)
    level: AutonomyLevel = "approve"
    revert_hours: int = Field(default=24, ge=0, le=24 * 30)
    note: str = Field(default="", max_length=300)
    enabled: bool = True

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in RULE_KINDS:
            raise ValueError(f"unknown approval kind {value!r}")
        return value

    def applies_to(self, kind: str) -> bool:
        return self.kind in ("*", kind)

    def automatic(self) -> bool:
        return self.enabled and self.level != "approve"


class PolicyDecision(StrictModel):
    level: AutonomyLevel
    rule_id: str | None = None
    reason: str

    @property
    def automatic(self) -> bool:
        return self.level != "approve"


class AutonomyPolicy(StrictModel):
    rules: list[AutonomyRule] = []

    @field_validator("rules")
    @classmethod
    def _unique_ids(cls, rules: list[AutonomyRule]) -> list[AutonomyRule]:
        ids = [r.id for r in rules]
        if len(set(ids)) != len(ids):
            raise ValueError("rule ids must be unique")
        return rules

    def provider(self) -> Callable[[], Awaitable[AutonomyPolicy]]:
        """This policy as what the approval gateway takes (fixed, for tests and tools)."""

        async def current() -> AutonomyPolicy:
            return self

        return current

    def rule(self, rule_id: str) -> AutonomyRule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def decide(self, kind: str, facts: ActionFacts | None = None) -> PolicyDecision:
        """First enabled rule for ``kind`` whose conditions hold; ``approve`` otherwise."""
        facts = facts or ActionFacts()
        if kind in NEVER_AUTOMATED:
            return PolicyDecision(level="approve", reason="always a person's decision")
        for rule in self.rules:
            if not rule.enabled or not rule.applies_to(kind):
                continue
            if rule.when.matches(facts):
                return PolicyDecision(
                    level=rule.level,
                    rule_id=rule.id,
                    reason=rule.note or f"rule {rule.id}: {rule.when.describe()}",
                )
        return PolicyDecision(level="approve", reason="no rule matched")

    def raises_over(self, current: AutonomyPolicy) -> list[str]:
        """Rules that widen autonomy compared to ``current``: a new automatic rule, a
        higher level, a changed condition or kind on an automatic rule, or an
        automatic rule switched back on. Lowering is never in the list."""
        widened: list[str] = []
        for rule in self.rules:
            if not rule.automatic():
                continue
            before = current.rule(rule.id)
            if before is None or not before.enabled:
                widened.append(rule.id)
            elif LEVEL_RANK[rule.level] > LEVEL_RANK[before.level]:
                widened.append(rule.id)
            elif (rule.when, rule.kind) != (before.when, before.kind):
                widened.append(rule.id)
            elif rule.revert_hours < before.revert_hours and rule.level == "auto_notice":
                widened.append(rule.id)  # a shorter window to catch it is more autonomy
        return widened

    @classmethod
    def from_legacy(
        cls,
        auto_send_partner_ids: list[int] | tuple[int, ...] = (),
        auto_send_kinds: list[str] | tuple[str, ...] = (),
        bill_auto_approve_amount: float | None = None,
    ) -> AutonomyPolicy:
        """The rules the phase 5 and phase 9 settings meant, so nothing changes on upgrade."""
        rules: list[AutonomyRule] = []
        if auto_send_partner_ids:
            rules.append(
                AutonomyRule(
                    id="legacy-auto-send-partners",
                    kind="send_email",
                    when=RuleConditions(partner_ids=sorted(set(auto_send_partner_ids))),
                    level="auto_notice",
                    note="Trusted suppliers: emails go out without approval",
                )
            )
        if auto_send_kinds:
            rules.append(
                AutonomyRule(
                    id="legacy-auto-send-kinds",
                    kind="send_email",
                    when=RuleConditions(email_kinds=sorted(set(auto_send_kinds))),
                    level="auto_notice",
                    note="Routine emails (reminders, date requests) need no approval",
                )
            )
        if bill_auto_approve_amount:
            rules.append(
                AutonomyRule(
                    id="legacy-bill-under-amount",
                    kind="vendor_bill",
                    when=RuleConditions(amount_max=bill_auto_approve_amount, confidence_min=1.0),
                    level="auto_notice",
                    note="A matching invoice under the amount cap is recorded as a draft bill",
                )
            )
        return cls(rules=rules)
