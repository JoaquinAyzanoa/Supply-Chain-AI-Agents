"""Delegated auth: a personal (or work) account signs in once, tokens are refreshed silently.

Flow:

1. ``login_interactive`` runs the device-code flow. It prints a URL and a
   code; the mailbox owner signs in there as the bot account and accepts the
   five delegated permissions. MSAL stores the resulting refresh token in
   its cache, which we persist through a ``TokenCacheStore``.
2. Every later ``access_token`` call is silent: MSAL uses the cached refresh
   token, and we persist the cache again whenever it changed. Personal
   accounts keep refresh tokens alive as long as they are used at least
   every 90 days; the 30-minute mail sync guarantees that.

MSAL is synchronous; calls run in a worker thread so the event loop is not
blocked during token refreshes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import msal
from loguru import logger

from sc_core.infra.settings import MailCfg
from sc_core.mail.auth.base import TokenCacheStore
from sc_core.mail.errors import GraphError, MailAuthRequired
from sc_core.shared.errors import ConfigurationError

# MSAL adds openid/profile/offline_access itself and rejects them as explicit scopes.
_RESERVED = {"openid", "profile", "offline_access"}


class DelegatedTokenProvider:
    def __init__(self, cfg: MailCfg, store: TokenCacheStore) -> None:
        if not cfg.client_id:
            raise ConfigurationError("SC__MAIL__CLIENT_ID is empty; register the app first")
        self._cfg = cfg
        self._store = store
        self._scopes = [s for s in cfg.scopes if s not in _RESERVED]
        self._cache = msal.SerializableTokenCache()
        blob = store.load()
        if blob:
            self._cache.deserialize(blob)
        self._app = msal.PublicClientApplication(
            cfg.client_id, authority=cfg.authority, token_cache=self._cache
        )

    # --- TokenProvider ------------------------------------------------------

    def mailbox_prefix(self) -> str:
        return "/me"

    async def identity(self) -> str | None:
        return await asyncio.to_thread(self.account_username)

    async def access_token(self, *, force_refresh: bool = False) -> str:
        return await asyncio.to_thread(self._acquire_silent, force_refresh)

    # --- synchronous core -----------------------------------------------------

    def account_username(self) -> str | None:
        accounts = self._app.get_accounts()
        return accounts[0].get("username") if accounts else None

    def _acquire_silent(self, force_refresh: bool) -> str:
        accounts = self._app.get_accounts()
        if not accounts:
            raise MailAuthRequired("no cached mail session; run `just mail-login`")
        result = self._app.acquire_token_silent(
            self._scopes, account=accounts[0], force_refresh=force_refresh
        )
        self._persist()
        if not result or "access_token" not in result:
            description = (result or {}).get("error_description", "silent token refresh failed")
            raise MailAuthRequired(
                f"mail session expired or revoked; run `just mail-login` ({description})"
            )
        return str(result["access_token"])

    def login_interactive(self, prompt: Callable[[str], None] = print) -> str:
        """Blocking device-code login. Returns the signed-in username.

        ``prompt`` receives the human-readable instruction (URL + code); it
        defaults to printing, the CLI passes it through, tests capture it.
        """
        flow = self._app.initiate_device_flow(scopes=self._scopes)
        if "user_code" not in flow:
            raise GraphError(
                "could not start the device-code flow: "
                + str(flow.get("error_description") or flow),
                retryable=False,
            )
        prompt(flow["message"])
        result: dict[str, Any] = self._app.acquire_token_by_device_flow(flow)
        self._persist()
        if "access_token" not in result:
            raise MailAuthRequired(
                f"login failed: {result.get('error_description') or result.get('error')}"
            )
        username = (result.get("id_token_claims") or {}).get("preferred_username") or (
            self.account_username() or "unknown"
        )
        logger.bind(username=username).info("mail session established")
        return str(username)

    def logout(self) -> None:
        for account in self._app.get_accounts():
            self._app.remove_account(account)
        self._store.clear()

    def _persist(self) -> None:
        if self._cache.has_state_changed:
            self._store.save(self._cache.serialize())
            self._cache.has_state_changed = False
