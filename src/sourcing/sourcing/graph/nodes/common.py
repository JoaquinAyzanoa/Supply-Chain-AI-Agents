"""Helpers shared by the nodes: the task, the round, the limits, terminal updates."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sc_core.graph import clear_sensitive
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.schema.a2a import Outcome, OutcomeStatus, SourcingTask
from sc_core.schema.runtime_settings import RuntimeSettings
from sourcing.domain.models import BasketLine, OrderRef, Round


@dataclass(frozen=True)
class Limits:
    """What the Control Tower settings say a round and a negotiation may do."""

    top_n: int = 3
    deadline_days: int = 5
    freight_pct: float = 5.0
    cap_pct: float = 10.0
    max_rounds: int = 2

    @classmethod
    def from_runtime(cls, settings: RuntimeSettings) -> Limits:
        return cls(
            top_n=settings.sourcing_top_n,
            deadline_days=settings.sourcing_deadline_days,
            freight_pct=settings.sourcing_freight_pct,
            cap_pct=settings.negotiation_cap_pct,
            max_rounds=settings.negotiation_max_rounds,
        )


LimitsReader = Callable[[], Awaitable[Limits]]


def limits_from(runtime: RuntimeSettingsReader | None) -> LimitsReader:
    async def read() -> Limits:
        if runtime is None:
            return Limits()
        return Limits.from_runtime(await runtime.current())

    return read


def task_of(state: dict[str, Any]) -> SourcingTask:
    return SourcingTask.model_validate(state["task"])


def order_of(state: dict[str, Any]) -> OrderRef | None:
    raw = state.get("order")
    return OrderRef.model_validate(raw) if raw else None


def basket_of(state: dict[str, Any]) -> list[BasketLine]:
    return [BasketLine.model_validate(b) for b in state.get("basket") or []]


def round_of(state: dict[str, Any]) -> Round:
    raw = state.get("round")
    if not raw:
        raise RuntimeError("round missing in the state")
    return Round.model_validate(raw)


def finish(status: OutcomeStatus, summary: str, **extra: Any) -> dict[str, Any]:
    return {
        "outcome": Outcome(status=status, summary=summary[:500]).model_dump(mode="json"),
        **clear_sensitive(),
        **extra,
    }


def fail(summary: str) -> dict[str, Any]:
    return finish("failed", summary)


def money(value: float | None, currency: str | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f} {currency}".strip()


__all__ = [
    "Limits",
    "LimitsReader",
    "basket_of",
    "fail",
    "finish",
    "limits_from",
    "money",
    "order_of",
    "round_of",
    "task_of",
]
