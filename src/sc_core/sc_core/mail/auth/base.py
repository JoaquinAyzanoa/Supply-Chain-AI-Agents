"""Contracts shared by the delegated and application auth modes."""

from __future__ import annotations

from typing import Protocol

GRAPH_SCOPE_DEFAULT = "https://graph.microsoft.com/.default"


class TokenProvider(Protocol):
    """Hands out Graph access tokens and knows which mailbox they address."""

    async def access_token(self, *, force_refresh: bool = False) -> str:
        """A valid bearer token, refreshed silently when needed.

        Raises ``MailAuthRequired`` when no session exists (delegated mode) and
        ``GraphError`` when the identity platform fails.
        """
        ...

    def mailbox_prefix(self) -> str:
        """``/me`` in delegated mode, ``/users/{mailbox}`` in application mode."""
        ...

    async def identity(self) -> str | None:
        """Who the tokens act as (a username or the mailbox), for logs and ``whoami``."""
        ...


class TokenCacheStore(Protocol):
    """Where the serialised MSAL cache lives between processes (synchronous)."""

    def load(self) -> str | None: ...

    def save(self, blob: str) -> None: ...

    def clear(self) -> None: ...
