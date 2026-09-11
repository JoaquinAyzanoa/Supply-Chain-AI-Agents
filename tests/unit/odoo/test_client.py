"""Tests for sc_core.odoo.client against a scripted transport."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from sc_core.odoo.client import OdooClient
from sc_core.odoo.errors import OdooRpcError
from sc_core.shared.errors import (
    BusinessRuleViolation,
    ConfigurationError,
    ExternalServiceError,
    Forbidden,
    NotFound,
    ValidationFailed,
)

from .conftest import LOGIN_OK, ScriptedOdoo, Sleeps, make_cfg, rpc_error, rpc_ok

Factory = Callable[..., OdooClient]


def test_requires_api_key() -> None:
    with pytest.raises(ConfigurationError, match="SC__ODOO__API_KEY"):
        OdooClient(make_cfg(api_key=SecretStr("")))


def test_repr_hides_secret(client_factory: Factory) -> None:
    assert "k3y" not in repr(client_factory())


async def test_execute_kw_payload_shape(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [LOGIN_OK, rpc_ok([{"id": 1, "name": "P00001"}])]
    client = client_factory()
    rows = await client.search_read(
        "purchase.order", [["state", "=", "purchase"]], ["name"], limit=5
    )

    assert rows == [{"id": 1, "name": "P00001"}]
    assert odoo.calls() == [("common", "login"), ("object", "execute_kw")]
    login = odoo.requests[0]["params"]["args"]
    assert login == ["scai", "sc_agent_bot", "k3y"]
    model, method, args, kwargs = odoo.execute_kw_args()
    assert (model, method) == ("purchase.order", "search_read")
    assert args == [[["state", "=", "purchase"]]]
    assert kwargs["fields"] == ["name"] and kwargs["limit"] == 5 and kwargs["offset"] == 0
    assert kwargs["context"] == {"tz": "America/Lima"}
    assert odoo.requests[1]["params"]["args"][:3] == ["scai", 8, "k3y"]


async def test_uid_is_cached(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [LOGIN_OK, rpc_ok(3), rpc_ok(4)]
    client = client_factory()
    assert await client.search_count("purchase.order", []) == 3
    assert await client.search_count("purchase.order", []) == 4
    assert odoo.calls().count(("common", "login")) == 1


async def test_login_failure_is_forbidden(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [rpc_ok(False)]
    with pytest.raises(Forbidden, match="login failed"):
        await client_factory().uid()


async def test_caller_context_overrides_defaults(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    odoo.script += [LOGIN_OK, rpc_ok(True)]
    await client_factory(lang="es_PE").execute(
        "purchase.order",
        "write",
        [1],
        {"x": 1},
        context={"tracking_disable": True, "lang": "en_US"},
    )
    _, _, _, kwargs = odoo.execute_kw_args()
    assert kwargs["context"] == {"lang": "en_US", "tz": "America/Lima", "tracking_disable": True}


async def test_none_result_is_allowed(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [LOGIN_OK, rpc_ok(None)]
    assert await client_factory().call("sc.approval", "action_approve", [5]) is None


async def test_create_returns_int(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [LOGIN_OK, rpc_ok(42), rpc_ok([43])]
    client = client_factory()
    assert await client.create("sc.agent.run", {"run_id": "r"}) == 42
    assert await client.create("sc.agent.run", {"run_id": "s"}) == 43


@pytest.mark.parametrize(
    ("odoo_name", "expected"),
    [
        ("odoo.exceptions.AccessError", Forbidden),
        ("odoo.exceptions.AccessDenied", Forbidden),
        ("odoo.exceptions.MissingError", NotFound),
        ("odoo.exceptions.ValidationError", ValidationFailed),
        ("odoo.exceptions.UserError", BusinessRuleViolation),
        ("odoo.exceptions.RedirectWarning", BusinessRuleViolation),
    ],
)
async def test_business_errors_are_mapped_and_not_retried(
    odoo: ScriptedOdoo, client_factory: Factory, sleeps: Sleeps, odoo_name: str, expected: type
) -> None:
    odoo.script += [LOGIN_OK, rpc_error(odoo_name, "nope")]
    with pytest.raises(expected) as exc:
        await client_factory().search_count("purchase.order", [])
    assert exc.value.message == "nope"
    assert exc.value.details["odoo_exception"] == odoo_name
    assert exc.value.retryable is False
    assert sleeps.delays == []


async def test_unknown_server_error_keeps_traceback(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    odoo.script += [LOGIN_OK, rpc_error("builtins.TypeError", "bad", debug="Traceback: line 1")]
    with pytest.raises(OdooRpcError) as exc:
        await client_factory().search_count("purchase.order", [])
    assert exc.value.retryable is False
    assert exc.value.details["traceback"].startswith("Traceback")
    assert exc.value.details["service"] == "odoo"


async def test_retries_on_5xx_then_succeeds(
    odoo: ScriptedOdoo, client_factory: Factory, sleeps: Sleeps
) -> None:
    odoo.script += [httpx.Response(503, text="down"), httpx.Response(502), LOGIN_OK, rpc_ok(1)]
    assert await client_factory().search_count("purchase.order", []) == 1
    assert sleeps.delays == [0.5, 1.0]


async def test_retries_on_transport_error(
    odoo: ScriptedOdoo, client_factory: Factory, sleeps: Sleeps
) -> None:
    odoo.script += [httpx.ConnectError("refused"), LOGIN_OK, rpc_ok(1)]
    assert await client_factory().search_count("purchase.order", []) == 1
    assert sleeps.delays == [0.5]


async def test_gives_up_after_max_retries(
    odoo: ScriptedOdoo, client_factory: Factory, sleeps: Sleeps
) -> None:
    odoo.script += [httpx.ReadTimeout("slow")] * 3
    with pytest.raises(ExternalServiceError) as exc:
        await client_factory(max_retries=2).version()
    assert exc.value.retryable is True
    assert "ReadTimeout" in exc.value.message
    assert sleeps.delays == [0.5, 1.0]
    assert odoo.script == []


async def test_4xx_is_not_retried(
    odoo: ScriptedOdoo, client_factory: Factory, sleeps: Sleeps
) -> None:
    odoo.script += [httpx.Response(404, text="wrong url")]
    with pytest.raises(ExternalServiceError) as exc:
        await client_factory().version()
    assert exc.value.retryable is False
    assert exc.value.details["status"] == 404
    assert sleeps.delays == []


async def test_non_json_body(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [httpx.Response(200, text="<html>proxy error</html>")]
    with pytest.raises(ExternalServiceError, match="non-JSON"):
        await client_factory().version()


async def test_iter_search_read_paginates(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    page1 = [{"id": i} for i in range(1, 3)]
    page2 = [{"id": 3}]
    odoo.script += [LOGIN_OK, rpc_ok(page1), rpc_ok(page2)]
    rows = [
        r async for r in client_factory().iter_search_read("res.partner", [], ["id"], batch_size=2)
    ]
    assert [r["id"] for r in rows] == [1, 2, 3]
    offsets = [odoo.execute_kw_args(i)[3]["offset"] for i in range(2)]
    orders = [odoo.execute_kw_args(i)[3]["order"] for i in range(2)]
    assert offsets == [0, 2] and orders == ["id asc", "id asc"]


async def test_iter_search_read_stops_on_full_last_page(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    odoo.script += [LOGIN_OK, rpc_ok([{"id": 1}, {"id": 2}]), rpc_ok([])]
    rows = [
        r async for r in client_factory().iter_search_read("res.partner", [], ["id"], batch_size=2)
    ]
    assert len(rows) == 2
    assert odoo.script == []


async def test_record_and_model_calls(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [LOGIN_OK, rpc_ok(True), rpc_ok(1)]
    client = client_factory()
    await client.call(
        "purchase.order.line", "sc_log_eta_change", [7], source="supplier", run_id="r1"
    )
    model, method, args, kwargs = odoo.execute_kw_args()
    assert (model, method, args) == ("purchase.order.line", "sc_log_eta_change", [[7]])
    assert kwargs["source"] == "supplier" and kwargs["run_id"] == "r1"
    await client.call_model("sc.agent.run", "sc_finish", "r1", "applied", "done")
    _, method, args, _ = odoo.execute_kw_args()
    assert (method, args) == ("sc_finish", ["r1", "applied", "done"])
