"""Typed repositories over the Odoo client, one per model family.

A repository converts between Odoo rows and the models in
:mod:`sc_core.odoo.models`, owns the domains it queries, and makes writes
idempotent where the business needs it (external references, unique keys).
Nothing here decides *whether* to write: that is the agents' and the
approvals' job.
"""

from sc_core.odoo.repositories.activity import ActivityRepo
from sc_core.odoo.repositories.agent_run import AgentRunRepo
from sc_core.odoo.repositories.approval import ApprovalRepo
from sc_core.odoo.repositories.mail_link import MailLinkRepo
from sc_core.odoo.repositories.orderpoint import OrderpointRepo
from sc_core.odoo.repositories.partner import PartnerRepo
from sc_core.odoo.repositories.picking import PickingRepo
from sc_core.odoo.repositories.planning import (
    DemandRepo,
    IncomingRepo,
    ProductRepo,
    QuantRepo,
    WarehouseRepo,
    supplier_terms,
)
from sc_core.odoo.repositories.purchase_order import PurchaseOrderRepo
from sc_core.odoo.repositories.supplierinfo import SupplierInfoRepo

__all__ = [
    "ActivityRepo",
    "AgentRunRepo",
    "ApprovalRepo",
    "DemandRepo",
    "IncomingRepo",
    "MailLinkRepo",
    "OrderpointRepo",
    "PartnerRepo",
    "PickingRepo",
    "ProductRepo",
    "PurchaseOrderRepo",
    "QuantRepo",
    "SupplierInfoRepo",
    "WarehouseRepo",
    "supplier_terms",
]
