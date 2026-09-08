"""Async JSON-RPC client for Odoo 17/18.

This is the only module that knows Odoo's wire format. Repositories call
``execute`` (or the thin wrappers) with model, method and arguments and get
plain Python values back; failures arrive as ``sc_core.shared.errors``.

Behaviour worth knowing:

- The user id is resolved once with the ``common`` service and cached.
- Every ORM call carries a default context (language, timezone) unless the
  caller provides its own keys.
- Transport failures and HTTP 5xx are retried with exponential backoff.
  JSON-RPC errors (access, validation, business rules, server bugs) are
  never retried: repeating them cannot change the outcome.
- The API key never appears in logs or reprs.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

import httpx
from loguru import logger

from sc_core.infra.settings import OdooCfg
from sc_core.odoo.errors import SERVICE, translate_rpc_error
from sc_core.shared.errors import ConfigurationError, ExternalServiceError, Forbidden

Domain = list[Any]  # Odoo search domain: [["field", "op", value], "|", ...]
Sleep = Callable[[float], Awaitable[None]]

_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 5.0


class OdooClient:
    def __init__(
        self,
        cfg: OdooCfg,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not cfg.configured:
            raise ConfigurationError(
                "SC__ODOO__API_KEY is empty; run `just odoo-apikey` after `just odoo-init`"
            )
        self._cfg = cfg
        self._key = cfg.api_key.get_secret_value()
        self._sleep = sleep
        self._ids = itertools.count(1)
        self._uid: int | None = None
        self._http = httpx.AsyncClient(
            base_url=cfg.url.rstrip("/"),
            timeout=cfg.timeout_seconds,
            transport=transport,
            headers={"Content-Type": "application/json"},
        )

    def __repr__(self) -> str:
        return f"OdooClient(url={self._cfg.url!r}, db={self._cfg.db!r}, login={self._cfg.login!r})"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> OdooClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- transport ----------------------------------------------------------

    async def _rpc(self, service: str, method: str, *args: Any) -> Any:
        """One JSON-RPC call with retries on transport errors and 5xx."""
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "call",
            "params": {"service": service, "method": method, "args": list(args)},
        }
        attempts = self._cfg.max_retries + 1
        last_error: ExternalServiceError | None = None
        for attempt in range(attempts):
            try:
                response = await self._http.post("/jsonrpc", json=payload)
            except httpx.TransportError as exc:  # connect, read, write, pool timeouts and errors
                last_error = ExternalServiceError(
                    f"odoo unreachable: {type(exc).__name__}", service=SERVICE
                )
            else:
                if response.status_code >= 500:
                    last_error = ExternalServiceError(
                        f"odoo returned HTTP {response.status_code}",
                        service=SERVICE,
                        details={"status": response.status_code},
                    )
                elif response.status_code >= 400:
                    raise ExternalServiceError(
                        f"odoo returned HTTP {response.status_code}",
                        service=SERVICE,
                        details={"status": response.status_code},
                        retryable=False,
                    )
                else:
                    return self._unwrap(response)
            if attempt < attempts - 1:
                delay = min(_BACKOFF_BASE * 2**attempt, _BACKOFF_CAP)
                logger.bind(
                    attempt=attempt + 1, delay=delay, service=service, method=method
                ).warning("odoo call failed, retrying: {}", last_error.message)
                await self._sleep(delay)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _unwrap(response: httpx.Response) -> Any:
        try:
            body = response.json()
        except ValueError as exc:
            raise ExternalServiceError(
                "odoo returned a non-JSON body", service=SERVICE, retryable=False
            ) from exc
        if "error" in body:
            raise translate_rpc_error(body["error"])
        # Odoo omits "result" when the method returned None.
        return body.get("result")

    # --- authentication and metadata ------------------------------------------

    async def version(self) -> dict[str, Any]:
        """Server version info; also the cheapest health probe (no login needed)."""
        return await self._rpc("common", "version")

    async def uid(self) -> int:
        if self._uid is None:
            uid = await self._rpc("common", "login", self._cfg.db, self._cfg.login, self._key)
            if not uid:
                raise Forbidden(
                    f"odoo login failed for {self._cfg.login!r} on db {self._cfg.db!r}",
                    details={"login": self._cfg.login, "db": self._cfg.db},
                )
            self._uid = int(uid)
        return self._uid

    # --- ORM ----------------------------------------------------------------

    def _context(self, provided: dict[str, Any] | None) -> dict[str, Any]:
        defaults: dict[str, Any] = {"tz": self._cfg.tz}
        if self._cfg.lang:
            defaults["lang"] = self._cfg.lang
        return {**defaults, **(provided or {})}

    async def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Call any ORM method through ``execute_kw``.

        ``kwargs`` are passed as keyword arguments to the Odoo method;
        ``context`` is merged with the client defaults.
        """
        kwargs["context"] = self._context(kwargs.get("context"))
        uid = await self.uid()
        return await self._rpc(
            "object", "execute_kw", self._cfg.db, uid, self._key, model, method, list(args), kwargs
        )

    async def search_read(
        self,
        model: str,
        domain: Domain,
        fields: Sequence[str],
        *,
        limit: int | None = None,
        offset: int = 0,
        order: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"fields": list(fields), "offset": offset}
        if limit is not None:
            kwargs["limit"] = limit
        if order is not None:
            kwargs["order"] = order
        if context is not None:
            kwargs["context"] = context
        return await self.execute(model, "search_read", domain, **kwargs)

    async def iter_search_read(
        self,
        model: str,
        domain: Domain,
        fields: Sequence[str],
        *,
        batch_size: int = 200,
        order: str = "id asc",
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every matching record, fetching ``batch_size`` rows at a time.

        A stable order is required so pagination does not skip or repeat rows
        while other users write; ``id asc`` is the default.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        offset = 0
        while True:
            rows = await self.search_read(
                model, domain, fields, limit=batch_size, offset=offset, order=order
            )
            for row in rows:
                yield row
            if len(rows) < batch_size:
                return
            offset += batch_size

    async def search(
        self, model: str, domain: Domain, *, limit: int | None = None, order: str | None = None
    ) -> list[int]:
        kwargs: dict[str, Any] = {}
        if limit is not None:
            kwargs["limit"] = limit
        if order is not None:
            kwargs["order"] = order
        return await self.execute(model, "search", domain, **kwargs)

    async def search_count(self, model: str, domain: Domain) -> int:
        return await self.execute(model, "search_count", domain)

    async def read(
        self, model: str, ids: Sequence[int], fields: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Read fields of the given ids.

        Odoo 18 silently omits ids that do not exist (or are not visible), so
        the result may be shorter than ``ids``. Writes and record methods on a
        missing id raise ``NotFound`` instead; repositories that need
        existence checks should compare lengths or use ``search``.
        """
        return await self.execute(model, "read", list(ids), fields=list(fields))

    async def create(self, model: str, values: dict[str, Any]) -> int:
        """Create one record and return its id."""
        result = await self.execute(model, "create", values)
        # Odoo 17+ returns a list when given a list; a dict yields a plain id.
        return int(result[0] if isinstance(result, list) else result)

    async def write(self, model: str, ids: Sequence[int], values: dict[str, Any]) -> bool:
        return bool(await self.execute(model, "write", list(ids), values))

    async def unlink(self, model: str, ids: Sequence[int]) -> bool:
        return bool(await self.execute(model, "unlink", list(ids)))

    async def call(self, model: str, method: str, ids: Sequence[int], **kwargs: Any) -> Any:
        """Call a record method (``self`` = the given ids), e.g. ``message_post``."""
        return await self.execute(model, method, list(ids), **kwargs)

    async def call_model(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Call an ``@api.model`` method (no ids), e.g. ``sc.agent.run.sc_finish``."""
        return await self.execute(model, method, *args, **kwargs)
