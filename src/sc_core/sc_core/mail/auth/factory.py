"""Pick the token provider for the configured auth mode."""

from __future__ import annotations

from sc_core.infra.settings import MailCfg
from sc_core.mail.auth.application import ApplicationTokenProvider
from sc_core.mail.auth.base import TokenCacheStore, TokenProvider
from sc_core.mail.auth.delegated import DelegatedTokenProvider
from sc_core.mail.auth.store import PostgresTokenCacheStore


def build_token_provider(
    cfg: MailCfg, *, app_db_dsn: str, store: TokenCacheStore | None = None
) -> TokenProvider:
    if cfg.auth_mode == "application":
        return ApplicationTokenProvider(cfg)
    return DelegatedTokenProvider(cfg, store or PostgresTokenCacheStore(app_db_dsn))
