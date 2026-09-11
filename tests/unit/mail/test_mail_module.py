"""MailModule wires the token provider, the Graph client and the readiness check."""

from injector import Injector

from sc_core.infra.health import HealthRegistry
from sc_core.infra.module import CoreModule, MailModule
from sc_core.infra.settings import Settings
from sc_core.mail.auth import DelegatedTokenProvider, MemoryTokenCacheStore, TokenProvider
from sc_core.mail.graph import GraphMailClient
from sc_core.mail.protocol import MailClient


def test_module_provides_singletons_and_registers_health() -> None:
    settings = Settings(
        _env_file=None,
        service_name="t",
        environment="test",
        mail={"client_id": "cid"},  # type: ignore[arg-type]
    )
    health = HealthRegistry()
    injector = Injector([CoreModule(settings, health), MailModule(store=MemoryTokenCacheStore())])

    client = injector.get(MailClient)  # type: ignore[type-abstract]
    assert isinstance(client, GraphMailClient)
    assert injector.get(MailClient) is client  # type: ignore[type-abstract]
    tokens = injector.get(TokenProvider)  # type: ignore[type-abstract]
    assert isinstance(tokens, DelegatedTokenProvider)
    assert "graph" in health.names()
