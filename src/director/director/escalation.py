"""Handing a case to a human.

``Escalator.escalate`` is called by the workflow when an agent fails or
returns ``escalated``, when the router decides no agent can act, or when a
policy limit is reached (P6-S5). The Odoo implementation (P6-S6) writes an
``sc.approval`` of kind ``escalation`` with a model-written summary and the
trace link; the in-memory one records the call for tests.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from loguru import logger

from director.store import Case
from sc_core.schema.base import StrictModel


class Escalation(StrictModel):
    summary: str
    approval_id: int | None = None
    trace_url: str | None = None


@runtime_checkable
class Escalator(Protocol):
    async def escalate(
        self, case: Case, *, reason: str, details: dict[str, Any] | None = None
    ) -> Escalation: ...


class MemoryEscalator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def escalate(
        self, case: Case, *, reason: str, details: dict[str, Any] | None = None
    ) -> Escalation:
        self.calls.append({"case_id": case.case_id, "reason": reason, "details": details or {}})
        return Escalation(summary=reason, approval_id=None)


class LoggingEscalator:
    """Until the Odoo escalator is wired: make the hand-off visible in the logs."""

    async def escalate(
        self, case: Case, *, reason: str, details: dict[str, Any] | None = None
    ) -> Escalation:
        logger.bind(case_id=case.case_id, po_name=case.po_name).warning(
            "case needs a human: {}", reason
        )
        return Escalation(summary=reason)
