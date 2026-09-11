"""Application auth (production, phase 10): client credentials on the company tenant.

No user signs in. The app registration holds application permissions
restricted to one shared mailbox by an Exchange application access policy,
and tokens are requested with the ``.default`` scope. Implemented here so the
rest of ``sc_core.mail`` is mode-agnostic; verified against a real tenant in
phase 10.
"""

from __future__ import annotations

import asyncio

import msal

from sc_core.infra.settings import MailCfg
from sc_core.mail.auth.base import GRAPH_SCOPE_DEFAULT
from sc_core.mail.errors import GraphError
from sc_core.shared.errors import ConfigurationError


class ApplicationTokenProvider:
    def __init__(self, cfg: MailCfg) -> None:
        if not (cfg.client_id and cfg.tenant_id and cfg.client_secret.get_secret_value()):
            raise ConfigurationError(
                "application mail mode needs SC__MAIL__CLIENT_ID, TENANT_ID and CLIENT_SECRET"
            )
        if cfg.mailbox in ("", "me"):
            raise ConfigurationError("application mail mode needs SC__MAIL__MAILBOX=<address>")
        self._mailbox = cfg.mailbox
        self._app = msal.ConfidentialClientApplication(
            cfg.client_id,
            authority=f"https://login.microsoftonline.com/{cfg.tenant_id}",
            client_credential=cfg.client_secret.get_secret_value(),
        )

    def mailbox_prefix(self) -> str:
        return f"/users/{self._mailbox}"

    async def identity(self) -> str | None:
        return self._mailbox

    async def access_token(self, *, force_refresh: bool = False) -> str:
        return await asyncio.to_thread(self._acquire, force_refresh)

    def _acquire(self, force_refresh: bool) -> str:
        result = None
        if not force_refresh:
            result = self._app.acquire_token_silent([GRAPH_SCOPE_DEFAULT], account=None)
        if not result:
            result = self._app.acquire_token_for_client(scopes=[GRAPH_SCOPE_DEFAULT])
        if "access_token" not in result:
            reason = result.get("error_description") or result.get("error")
            raise GraphError(f"client credentials failed: {reason}", retryable=False)
        return str(result["access_token"])
