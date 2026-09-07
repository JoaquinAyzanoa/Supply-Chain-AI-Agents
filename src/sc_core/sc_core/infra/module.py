"""Dependency injection wiring.

We use ``injector`` so that services declare what they need (settings, a
health registry, later an Odoo client or a Graph client) instead of building
them by hand in every ``main.py``. ``CoreModule`` provides what every service
has; later phases add ``OdooModule``, ``MailModule``, ``LlmModule`` that are
passed to ``create_application(modules=[...])``.
"""

from __future__ import annotations

from injector import Binder, Module, singleton

from sc_core.infra.health.registry import HealthRegistry
from sc_core.infra.settings import Settings


class CoreModule(Module):
    def __init__(self, settings: Settings, health: HealthRegistry | None = None) -> None:
        self._settings = settings
        self._health = health or HealthRegistry()

    def configure(self, binder: Binder) -> None:
        binder.bind(Settings, to=self._settings, scope=singleton)
        binder.bind(HealthRegistry, to=self._health, scope=singleton)
