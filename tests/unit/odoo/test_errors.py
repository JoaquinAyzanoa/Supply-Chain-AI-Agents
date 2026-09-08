"""Tests for sc_core.odoo.errors.translate_rpc_error."""

from sc_core.odoo.errors import OdooRpcError, translate_rpc_error
from sc_core.shared.errors import Forbidden


def test_known_exception_maps_with_arguments() -> None:
    err = translate_rpc_error(
        {
            "message": "Odoo Server Error",
            "data": {
                "name": "odoo.exceptions.AccessError",
                "message": "You are not allowed",
                "arguments": ["You are not allowed"],
            },
        }
    )
    assert isinstance(err, Forbidden)
    assert err.message == "You are not allowed"
    assert err.details == {
        "odoo_exception": "odoo.exceptions.AccessError",
        "arguments": ["You are not allowed"],
    }


def test_missing_data_falls_back_to_top_level_message() -> None:
    err = translate_rpc_error({"message": "Bad request"})
    assert isinstance(err, OdooRpcError)
    assert err.message == "Bad request"
    assert err.details["odoo_exception"] == ""


def test_traceback_is_truncated_to_tail() -> None:
    err = translate_rpc_error(
        {"data": {"name": "psycopg2.errors.Deadlock", "message": "x", "debug": "a" * 10_000}}
    )
    assert isinstance(err, OdooRpcError)
    assert len(err.details["traceback"]) == 4000
