"""Tests for the delegated and application token providers (MSAL faked)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from sc_core.infra.settings import MailCfg
from sc_core.mail.auth import (
    ApplicationTokenProvider,
    DelegatedTokenProvider,
    FileTokenCacheStore,
    MemoryTokenCacheStore,
    build_token_provider,
)
from sc_core.mail.errors import GraphError, MailAuthRequired
from sc_core.shared.errors import ConfigurationError

from . import fake_msal


@pytest.fixture(autouse=True)
def _fake_msal(monkeypatch: pytest.MonkeyPatch) -> None:
    from sc_core.mail.auth import application, delegated

    fake_msal.FakePublicClientApplication.instances.clear()
    monkeypatch.setattr(
        delegated.msal, "PublicClientApplication", fake_msal.FakePublicClientApplication
    )
    monkeypatch.setattr(
        application.msal,
        "ConfidentialClientApplication",
        fake_msal.FakeConfidentialClientApplication,
    )


def _cfg(**overrides: object) -> MailCfg:
    values: dict[str, object] = {
        "client_id": "cid",
        "authority": "https://login.microsoftonline.com/consumers",
    }
    values.update(overrides)
    return MailCfg(**values)  # type: ignore[arg-type]


def _last_app() -> fake_msal.FakePublicClientApplication:
    return fake_msal.FakePublicClientApplication.instances[-1]


# --- delegated ---------------------------------------------------------------


def test_requires_client_id() -> None:
    with pytest.raises(ConfigurationError):
        DelegatedTokenProvider(_cfg(client_id=""), MemoryTokenCacheStore())


async def test_no_session_raises_auth_required() -> None:
    provider = DelegatedTokenProvider(_cfg(), MemoryTokenCacheStore())
    assert provider.mailbox_prefix() == "/me"
    assert await provider.identity() is None
    with pytest.raises(MailAuthRequired, match="mail-login"):
        await provider.access_token()


async def test_login_persists_cache_and_survives_restart() -> None:
    store = MemoryTokenCacheStore()
    prompts: list[str] = []
    provider = DelegatedTokenProvider(_cfg(), store)
    username = provider.login_interactive(prompts.append)
    assert username == "scai.compras@outlook.com"
    assert prompts and "ABCD1234" in prompts[0]
    assert store.load() is not None  # cache written after login

    # a new process: fresh provider, same store -> silent token without any prompt
    again = DelegatedTokenProvider(_cfg(), store)
    assert await again.identity() == "scai.compras@outlook.com"
    token = await again.access_token()
    assert token.startswith("tok-")
    assert _last_app().silent_calls[0]["force"] is False
    assert _last_app().silent_calls[0]["scopes"] == [
        "Mail.Read",
        "Mail.ReadWrite",
        "Mail.Send",
        "User.Read",
    ]


async def test_refresh_updates_store_and_force_refresh_is_passed() -> None:
    store = MemoryTokenCacheStore()
    provider = DelegatedTokenProvider(_cfg(), store)
    provider.login_interactive(lambda _m: None)
    before = store.load()
    await provider.access_token(force_refresh=True)
    assert _last_app().silent_calls[-1]["force"] is True
    assert store.load() != before  # rotated cache persisted


async def test_revoked_session_raises_auth_required() -> None:
    provider = DelegatedTokenProvider(_cfg(), MemoryTokenCacheStore())
    provider.login_interactive(lambda _m: None)
    _last_app().fail_silent = True
    with pytest.raises(MailAuthRequired, match="revoked"):
        await provider.access_token()


def test_device_flow_errors() -> None:
    provider = DelegatedTokenProvider(_cfg(), MemoryTokenCacheStore())
    _last_app().flow_error = "client not allowed public flows"
    with pytest.raises(GraphError, match="public flows"):
        provider.login_interactive(lambda _m: None)
    _last_app().flow_error = None
    _last_app().fail_device = True
    with pytest.raises(MailAuthRequired, match="declined"):
        provider.login_interactive(lambda _m: None)


async def test_logout_clears_store() -> None:
    store = MemoryTokenCacheStore()
    provider = DelegatedTokenProvider(_cfg(), store)
    provider.login_interactive(lambda _m: None)
    provider.logout()
    assert store.load() is None
    with pytest.raises(MailAuthRequired):
        await provider.access_token()


def test_reserved_scopes_are_not_requested() -> None:
    provider = DelegatedTokenProvider(
        _cfg(scopes=["Mail.Read", "offline_access", "openid"]), MemoryTokenCacheStore()
    )
    assert provider._scopes == ["Mail.Read"]


def test_file_store_roundtrip(tmp_path: Path) -> None:
    store = FileTokenCacheStore(tmp_path / "cache" / "msal.json")
    assert store.load() is None
    store.save("{}")
    assert store.load() == "{}"
    store.clear()
    assert store.load() is None


# --- application -------------------------------------------------------------


def _app_cfg(**overrides: object) -> MailCfg:
    values: dict[str, object] = {
        "auth_mode": "application",
        "client_id": "cid",
        "tenant_id": "tid",
        "client_secret": SecretStr("s3cret"),
        "mailbox": "compras@empresa.com",
    }
    values.update(overrides)
    return MailCfg(**values)  # type: ignore[arg-type]


async def test_application_provider() -> None:
    provider = ApplicationTokenProvider(_app_cfg())
    assert provider.mailbox_prefix() == "/users/compras@empresa.com"
    assert await provider.identity() == "compras@empresa.com"
    assert await provider.access_token() == "app-tok"
    assert provider._app.authority == "https://login.microsoftonline.com/tid"  # type: ignore[attr-defined]


def test_application_requires_tenant_secret_and_mailbox() -> None:
    with pytest.raises(ConfigurationError):
        ApplicationTokenProvider(_app_cfg(client_secret=SecretStr("")))
    with pytest.raises(ConfigurationError):
        ApplicationTokenProvider(_app_cfg(mailbox="me"))


async def test_application_failure_is_graph_error() -> None:
    provider = ApplicationTokenProvider(_app_cfg())
    provider._app.fail = True  # type: ignore[attr-defined]
    with pytest.raises(GraphError, match="bad secret"):
        await provider.access_token()


def test_factory_picks_mode() -> None:
    delegated = build_token_provider(
        _cfg(), app_db_dsn="postgresql://x", store=MemoryTokenCacheStore()
    )
    assert isinstance(delegated, DelegatedTokenProvider)
    assert isinstance(
        build_token_provider(_app_cfg(), app_db_dsn="postgresql://x"), ApplicationTokenProvider
    )


def test_mail_cfg_configured() -> None:
    assert MailCfg(client_id="").configured is False
    assert _cfg().configured is True
    assert _app_cfg(tenant_id="").configured is False
    assert _app_cfg().configured is True
