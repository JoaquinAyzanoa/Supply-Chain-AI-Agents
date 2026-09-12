"""The "check the mailbox" button: an approver triggers a sync and reads what it found."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import hash_password
from director.api.mailbox import describe
from director.testing import MemoryDirectorModule
from sc_core.app import create_application
from sc_core.infra.settings import Settings


@pytest.fixture
def module() -> MemoryDirectorModule:
    return MemoryDirectorModule()


@pytest.fixture
def client(module: MemoryDirectorModule) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        service_name="director",
        environment="test",
        events={"signing_secret": SecretStr("events")},
        ui={"jwt_secret": SecretStr("ui")},
    )
    app = create_application(settings, version=__version__, routers=[api_router], modules=[module])
    with TestClient(app) as c:
        yield c
    logger.remove()


async def _users(module: MemoryDirectorModule) -> None:
    for email, role in (("ana@x.com", "approver"), ("vic@x.com", "viewer")):
        await module.users.create(
            email=email,
            name=email.split("@")[0].title(),
            password_hash=hash_password("s3cret!!"),
            role=role,  # type: ignore[arg-type]
        )


def _token(client: TestClient, email: str) -> dict[str, str]:
    body = client.post("/api/auth/login", json={"email": email, "password": "s3cret!!"}).json()
    return {"Authorization": f"Bearer {body['token']}"}


async def test_approver_reads_the_mailbox_now(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    assert client.post("/api/mailbox/sync", headers=_token(client, "vic@x.com")).status_code == 403

    module.mailbox.report = {
        "status": "ok",
        "fetched": 3,
        "linked": 2,
        "unlinked": 1,
        "ignored": 0,
        "errors": 0,
        "linked_po_names": ["P00067", "P00068"],
    }
    answer = client.post("/api/mailbox/sync", headers=_token(client, "ana@x.com"))
    assert answer.status_code == 200
    body = answer.json()
    assert body["fetched"] == 3 and body["linked_po_names"] == ["P00067", "P00068"]
    assert (
        body["message"] == "3 new emails; 2 linked to orders (P00067, P00068); 1 without an order"
    )
    assert module.mailbox.calls == ["ana@x.com"]

    module.mailbox.report = {"status": "skipped_locked"}
    locked = client.post("/api/mailbox/sync", headers=_token(client, "ana@x.com")).json()
    assert locked["message"].startswith("the mailbox is being read right now")

    module.mailbox.fail = True
    down = client.post("/api/mailbox/sync", headers=_token(client, "ana@x.com"))
    assert down.status_code == 502 and "did not answer" in down.json()["detail"]


def test_describe_reads_like_a_sentence() -> None:
    assert describe({"status": "ok", "fetched": 0}) == "no new emails"
    assert describe({"status": "ok", "fetched": 1, "ignored": 1}) == "1 new email; 1 ignored"
    assert describe({"fetched": 2, "errors": 2}) == "2 new emails; 2 failed"
