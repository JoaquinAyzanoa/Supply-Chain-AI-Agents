"""Dependency injection wiring.

We use ``injector`` so that services declare what they need (settings, a
health registry, an Odoo client, repositories) instead of building them by
hand in every ``main.py``. ``CoreModule`` provides what every service has;
``OdooModule`` adds the Odoo client and repositories and registers the Odoo
readiness check. Later phases add ``MailModule`` and ``LlmModule``.
"""

from __future__ import annotations

from injector import Binder, Module, provider, singleton

from sc_core.infra.health.registry import HealthRegistry
from sc_core.infra.settings import Settings


class CoreModule(Module):
    def __init__(self, settings: Settings, health: HealthRegistry | None = None) -> None:
        self._settings = settings
        self._health = health or HealthRegistry()

    def configure(self, binder: Binder) -> None:
        binder.bind(Settings, to=self._settings, scope=singleton)
        binder.bind(HealthRegistry, to=self._health, scope=singleton)


class OdooModule(Module):
    """Odoo client (one per process) and the repositories built on it."""

    def __init__(self, *, critical: bool = True) -> None:
        self._critical = critical

    @provider
    @singleton
    def provide_client(self, settings: Settings, health: HealthRegistry) -> OdooClient:
        from sc_core.infra.health import checks
        from sc_core.odoo.client import OdooClient

        client = OdooClient(settings.odoo)
        health.register("odoo", checks.odoo(client), critical=self._critical)
        return client

    @provider
    def purchase_orders(self, client: OdooClient) -> PurchaseOrderRepo:
        return PurchaseOrderRepo(client)

    @provider
    def supplier_info(self, client: OdooClient) -> SupplierInfoRepo:
        return SupplierInfoRepo(client)

    @provider
    def orderpoints(self, client: OdooClient) -> OrderpointRepo:
        return OrderpointRepo(client)

    @provider
    def pickings(self, client: OdooClient) -> PickingRepo:
        return PickingRepo(client)

    @provider
    def partners(self, client: OdooClient) -> PartnerRepo:
        return PartnerRepo(client)

    @provider
    def activities(self, client: OdooClient) -> ActivityRepo:
        return ActivityRepo(client)

    @provider
    def agent_runs(self, client: OdooClient) -> AgentRunRepo:
        return AgentRunRepo(client)

    @provider
    def approvals(self, client: OdooClient) -> ApprovalRepo:
        return ApprovalRepo(client)

    @provider
    def mail_links(self, client: OdooClient) -> MailLinkRepo:
        return MailLinkRepo(client)


# Imported after the classes so the provider annotations resolve at runtime
# without a circular import at module load (repositories import settings).
from sc_core.odoo.client import OdooClient  # noqa: E402
from sc_core.odoo.repositories import (  # noqa: E402
    ActivityRepo,
    AgentRunRepo,
    ApprovalRepo,
    MailLinkRepo,
    OrderpointRepo,
    PartnerRepo,
    PickingRepo,
    PurchaseOrderRepo,
    SupplierInfoRepo,
)
