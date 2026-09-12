"""Settings people change from the Control Tower at runtime.

``RuntimeSettings`` is the editable subset of the configuration: the model
per agent, the follow-up policy, the suppliers whose emails go out without
approval and optional planning defaults. Everything else stays in ``.env``.
Version 0 mirrors the environment (``from_settings``); every ``PUT`` from
the UI appends a row to ``settings_history`` and the services pick it up
through :class:`sc_core.infra.runtime_settings.RuntimeSettingsReader`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from sc_core.schema.base import StrictModel

if TYPE_CHECKING:
    from sc_core.infra.settings import Settings


class RuntimeSettings(StrictModel):
    model_by_agent: dict[str, str] = Field(
        default_factory=dict, description="agent name -> model name from the registry"
    )
    rfq_no_reply_days: list[int] = [3, 7]
    po_eta_request_before_days: int = Field(default=5, ge=0)
    po_late_days: list[int] = [1, 4]
    approval_stale_days: int = Field(default=2, ge=0)
    approval_expire_days: int = Field(default=7, ge=0)
    max_actions_per_run: int = Field(default=20, ge=1)
    auto_send_partner_ids: list[int] = []
    # Planning defaults: None keeps the ABC class defaults; a value replaces them for
    # products a planner has not tuned (params with source "default").
    planning_service_level: float | None = Field(default=None, gt=0.5, lt=1.0)
    planning_review_period_days: int | None = Field(default=None, ge=1)
    planning_max_coverage_days: int | None = Field(default=None, ge=1)

    @classmethod
    def from_settings(cls, settings: Settings) -> RuntimeSettings:
        """What the environment says, as the version-0 baseline."""
        return cls(
            model_by_agent={k.lower(): v for k, v in settings.llm.model.items()},
            rfq_no_reply_days=list(settings.director.rfq_no_reply_days),
            po_eta_request_before_days=settings.director.po_eta_request_before_days,
            po_late_days=list(settings.director.po_late_days),
            approval_stale_days=settings.director.approval_stale_days,
            approval_expire_days=settings.director.approval_expire_days,
            max_actions_per_run=settings.director.max_actions_per_run,
            auto_send_partner_ids=list(settings.supplier_comms.auto_send_partner_ids),
        )

    def model_for(self, agent_name: str) -> str | None:
        """The model chosen for ``agent_name`` in the UI, or ``None`` for the environment's."""
        return self.model_by_agent.get(agent_name.lower())
