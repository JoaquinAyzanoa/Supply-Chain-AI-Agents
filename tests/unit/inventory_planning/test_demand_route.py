"""``GET /planning/demand/{product_id}``: one point per day, behind the bearer token."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from injector import Binder, Module, singleton
from loguru import logger
from pydantic import SecretStr

from inventory_planning import __version__
from inventory_planning.graph import Deps
from inventory_planning.routers import demand
from inventory_planning.testing import FakeDataPorts, FakeWritePorts, demo_ports
from sc_core.app import create_application
from sc_core.graph import ApprovalGateway
from sc_core.infra.settings import LangfuseCfg, PlanningCfg, Settings
from sc_core.llm.testing import ScriptedChatClient
from sc_core.odoo.models import DailyDemand
from tests.unit.graph.toy import FakeApprovalPorts

AS_OF = date(2026, 9, 14)


class DepsModule(Module):
    def __init__(self, data: FakeDataPorts) -> None:
        from inventory_planning.policy import MemoryParamsStore
        from inventory_planning.runs import MemoryRunStore

        self.deps = Deps(
            data=data,
            writes=FakeWritePorts(),
            params=MemoryParamsStore(),
            runs=MemoryRunStore(),
            chat=ScriptedChatClient(),
            approvals=ApprovalGateway(
                FakeApprovalPorts(),
                agent_name="inventory_planning",
                callback_url="http://x/approvals/callback",
                callback_secret="s",
                approver_user_id=2,
                deadline_days=2,
            ),
            cfg=PlanningCfg(product_category="", history_days=730),
            langfuse=LangfuseCfg(enabled=False),
            today=lambda: AS_OF,
        )

    def configure(self, binder: Binder) -> None:
        binder.bind(Deps, to=self.deps, scope=singleton)


@pytest.fixture
def data() -> FakeDataPorts:
    return demo_ports(as_of=AS_OF)


@pytest.fixture
def client(data: FakeDataPorts) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        service_name="inventory_planning",
        environment="test",
        events={"signing_secret": SecretStr("events")},
        a2a={"token": SecretStr("a2a-token")},
    )
    app = create_application(
        settings, version=__version__, routers=[demand.router], modules=[DepsModule(data)]
    )
    with TestClient(app) as c:
        yield c
    logger.remove()


def test_demand_history_fills_the_calendar_and_needs_the_token(
    client: TestClient, data: FakeDataPorts
) -> None:
    product = data.products_[0].id
    data.demand = [
        d for d in data.demand if d.product_id != product or d.day < date(2026, 9, 1)
    ] + [
        DailyDemand(product_id=product, day=date(2026, 9, 10), ordered=4, delivered=4),
        DailyDemand(product_id=product, day=date(2026, 9, 10), ordered=2, delivered=0),
        DailyDemand(product_id=product, day=date(2026, 9, 13), ordered=1, delivered=1),
    ]
    assert client.get(f"/planning/demand/{product}").status_code == 401
    bad = client.get(f"/planning/demand/{product}", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401

    body: dict[str, Any] = client.get(
        f"/planning/demand/{product}?days=14", headers={"Authorization": "Bearer a2a-token"}
    ).json()
    assert body["product_id"] == product and body["since"] == "2026-08-31"
    assert body["until"] == "2026-09-14" and len(body["days"]) == 15
    by_day = {d["day"]: d for d in body["days"]}
    assert by_day["2026-09-10"] == {"day": "2026-09-10", "ordered": 6.0, "delivered": 4.0}
    assert by_day["2026-09-13"]["ordered"] == 1.0 and by_day["2026-09-12"]["ordered"] == 0.0
    assert ("daily_sales", ([product], date(2026, 8, 31))) in data.calls
