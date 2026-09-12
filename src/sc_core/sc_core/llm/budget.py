"""Per-run spend guard.

A ``RunBudget`` lives in the trace context of a run. The chat client adds
every call's usage to it and refuses the next call once a limit is crossed,
raising ``BudgetExceeded`` so the agent can stop and escalate instead of
looping. Limits come from settings and may be lowered per agent, never
raised.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

from sc_core.shared.errors import BudgetExceeded


@dataclass
class RunBudget:
    max_input_tokens: int
    max_output_tokens: int
    max_usd: float
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    calls: int = 0

    def check(self) -> None:
        """Raise when any limit is already crossed (called before a request)."""
        if self.input_tokens > self.max_input_tokens:
            raise BudgetExceeded(
                "input token budget exhausted",
                details={"used": self.input_tokens, "limit": self.max_input_tokens},
            )
        if self.output_tokens > self.max_output_tokens:
            raise BudgetExceeded(
                "output token budget exhausted",
                details={"used": self.output_tokens, "limit": self.max_output_tokens},
            )
        if self.usd > self.max_usd:
            raise BudgetExceeded(
                "cost budget exhausted", details={"used_usd": self.usd, "limit_usd": self.max_usd}
            )

    def record(self, *, input_tokens: int, output_tokens: int, usd: float) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.usd += usd
        self.calls += 1

    def snapshot(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usd": round(self.usd, 6),
        }


@dataclass
class _Holder:
    budget: RunBudget | None = field(default=None)


current_budget: ContextVar[RunBudget | None] = ContextVar("llm_budget", default=None)


def get_budget() -> RunBudget | None:
    return current_budget.get()
