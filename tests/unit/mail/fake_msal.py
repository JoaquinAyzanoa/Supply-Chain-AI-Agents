"""A stand-in for msal's application classes.

It keeps its 'logged in' state inside the real ``msal.SerializableTokenCache``
it is given (the cache round-trips any JSON), so persistence through a store
can be asserted exactly as with the real library.
"""

from __future__ import annotations

import json
from typing import Any

import msal


class FakePublicClientApplication:
    instances: list[FakePublicClientApplication] = []

    def __init__(self, client_id: str, *, authority: str, token_cache: msal.SerializableTokenCache):
        self.client_id, self.authority, self.token_cache = client_id, authority, token_cache
        self.silent_calls: list[dict[str, Any]] = []
        self.fail_silent = False
        self.fail_device = False
        self.flow_error: str | None = None
        FakePublicClientApplication.instances.append(self)

    # cache-backed state -------------------------------------------------------
    def _state(self) -> dict[str, Any]:
        return json.loads(self.token_cache.serialize() or "{}")

    def _set_state(self, state: dict[str, Any]) -> None:
        self.token_cache.deserialize(json.dumps(state))
        self.token_cache.has_state_changed = True

    # msal surface -------------------------------------------------------------
    def get_accounts(self) -> list[dict[str, Any]]:
        user = self._state().get("fake_user")
        return [{"username": user, "home_account_id": "h"}] if user else []

    def acquire_token_silent(self, scopes, account=None, force_refresh=False, **_: Any):
        self.silent_calls.append({"scopes": scopes, "account": account, "force": force_refresh})
        if self.fail_silent:
            return {"error": "invalid_grant", "error_description": "refresh token revoked"}
        state = self._state()
        state["refreshes"] = state.get("refreshes", 0) + 1
        self._set_state(state)  # a refresh rotates the token: cache changed
        return {"access_token": f"tok-{state['refreshes']}"}

    def initiate_device_flow(self, scopes):
        if self.flow_error:
            return {"error": "invalid_client", "error_description": self.flow_error}
        return {
            "user_code": "ABCD1234",
            "message": "go to https://microsoft.com/devicelogin and enter ABCD1234",
            "scopes": scopes,
        }

    def acquire_token_by_device_flow(self, flow):
        if self.fail_device:
            return {"error": "authorization_declined", "error_description": "user declined"}
        self._set_state({"fake_user": "scai.compras@outlook.com", "refreshes": 0})
        return {
            "access_token": "tok-0",
            "id_token_claims": {"preferred_username": "scai.compras@outlook.com"},
        }

    def remove_account(self, account):
        self._set_state({})


class FakeConfidentialClientApplication:
    def __init__(self, client_id: str, *, authority: str, client_credential: str):
        self.client_id, self.authority, self.client_credential = (
            client_id,
            authority,
            client_credential,
        )
        self.calls: list[str] = []
        self.fail = False

    def acquire_token_silent(self, scopes, account=None, **_: Any):
        self.calls.append("silent")
        return None

    def acquire_token_for_client(self, scopes):
        self.calls.append("client")
        if self.fail:
            return {"error": "invalid_client", "error_description": "bad secret"}
        return {"access_token": "app-tok", "scopes": scopes}
