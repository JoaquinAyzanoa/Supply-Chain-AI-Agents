"""Token providers for Microsoft Graph."""

from sc_core.mail.auth.application import ApplicationTokenProvider
from sc_core.mail.auth.base import TokenCacheStore, TokenProvider
from sc_core.mail.auth.delegated import DelegatedTokenProvider
from sc_core.mail.auth.factory import build_token_provider
from sc_core.mail.auth.store import (
    FileTokenCacheStore,
    MemoryTokenCacheStore,
    PostgresTokenCacheStore,
)

__all__ = [
    "ApplicationTokenProvider",
    "DelegatedTokenProvider",
    "FileTokenCacheStore",
    "MemoryTokenCacheStore",
    "PostgresTokenCacheStore",
    "TokenCacheStore",
    "TokenProvider",
    "build_token_provider",
]
