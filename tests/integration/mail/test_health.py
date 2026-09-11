"""The graph readiness check against the real session."""

import pytest

from sc_core.infra.health import HealthRegistry, OverallStatus, checks
from sc_core.mail.graph import GraphMailClient

pytestmark = [pytest.mark.integration, pytest.mark.graph]


async def test_graph_health_check_passes(graph_client: GraphMailClient) -> None:
    registry = HealthRegistry()
    registry.register("graph", checks.graph(graph_client))
    report = await registry.run(service="t", version="1")
    assert report.status is OverallStatus.OK, report.model_dump()
