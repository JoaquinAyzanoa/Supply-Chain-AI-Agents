"""Odoo integration: JSON-RPC client, error mapping and (from P1-S5) typed repositories.

Nothing outside this package builds RPC payloads or inspects Odoo error
objects. Services depend on repositories; repositories depend on
:class:`OdooClient`.
"""

from sc_core.odoo.client import OdooClient
from sc_core.odoo.errors import OdooRpcError, translate_rpc_error

__all__ = ["OdooClient", "OdooRpcError", "translate_rpc_error"]
