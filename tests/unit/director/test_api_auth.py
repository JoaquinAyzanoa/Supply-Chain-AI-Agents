"""Login, tokens, expiry and roles on the Control Tower API."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import LoginRateLimit, User, hash_password, issue_token, verify_password
from director.testing import MemoryDirectorModule
from sc_core.app import create_application
from sc_core.infra.settings import Settings

SECRET = "ui-secret"


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
        ui={"jwt_secret": SecretStr(SECRET), "login_rate_per_minute": 3},
    )
    module.login_limit = LoginRateLimit(per_minute=3)
    app = create_application(settings, version=__version__, routers=[api_router], modules=[module])
    with TestClient(app) as c:
        yield c
    logger.remove()


async def _seed(module: MemoryDirectorModule) -> None:
    for email, role in (("ana@x.com", "approver"), ("vic@x.com", "viewer"), ("adm@x.com", "admin")):
        await module.users.create(
            email=email,
            name=email.split("@")[0].title(),
            password_hash=hash_password("s3cret!!"),
            role=role,  # type: ignore[arg-type]
        )


def _login(client: TestClient, email: str, password: str = "s3cret!!") -> dict:
    return client.post("/api/auth/login", json={"email": email, "password": password}).json()


def test_password_hashing_round_trip() -> None:
    hashed = hash_password("hunter22")
    assert hashed.startswith("$argon2") and verify_password("hunter22", hashed)
    assert not verify_password("hunter23", hashed)


async def test_login_issues_a_token_and_me_decodes_it(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _seed(module)
    body = _login(client, "ana@x.com")
    assert body["role"] == "approver" and body["name"] == "Ana" and body["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200 and me.json() == {
        "email": "ana@x.com",
        "name": "Ana",
        "role": "approver",
    }
    assert module.users.logins == [1]


async def test_bad_password_unknown_user_and_missing_token(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _seed(module)
    assert (
        client.post("/api/auth/login", json={"email": "ana@x.com", "password": "nope"}).status_code
        == 401
    )
    assert (
        client.post("/api/auth/login", json={"email": "ghost@x.com", "password": "x"}).status_code
        == 401
    )
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_expired_token_is_refused(client: TestClient) -> None:
    user = User(id=1, email="ana@x.com", name="Ana", role="approver")
    old = issue_token(
        user, secret=SECRET, ttl_minutes=5, now=datetime.now(UTC) - timedelta(hours=1)
    )
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {old}"})
    assert response.status_code == 401 and response.json()["detail"] == "token expired"
    wrong = issue_token(user, secret="other", ttl_minutes=5)
    assert (
        client.get("/api/auth/me", headers={"Authorization": f"Bearer {wrong}"}).status_code == 401
    )


async def test_roles_are_enforced_on_settings(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _seed(module)
    viewer = _login(client, "vic@x.com")["token"]
    admin = _login(client, "adm@x.com")["token"]
    update = {"settings": {"rfq_no_reply_days": [2, 5]}, "note": "faster"}
    assert client.put("/api/settings", json=update, headers=_auth(viewer)).status_code == 403
    assert client.get("/api/settings", headers=_auth(viewer)).json()["version"] == 0
    saved = client.put("/api/settings", json=update, headers=_auth(admin))
    assert saved.status_code == 200 and saved.json()["version"] == 1
    assert saved.json()["settings"]["rfq_no_reply_days"] == [2, 5]
    assert saved.json()["changed_by"] == "adm@x.com" and saved.json()["note"] == "faster"
    current = client.get("/api/settings", headers=_auth(viewer)).json()
    assert current["version"] == 1 and current["settings"]["po_late_days"] == [1, 4]
    history = client.get("/api/settings/history", headers=_auth(viewer)).json()
    assert [h["version"] for h in history] == [1]
    assert (
        client.put(
            "/api/settings",
            json={"settings": {"planning_service_level": 1.5}},
            headers=_auth(admin),
        ).status_code
        == 422
    )


async def test_login_is_rate_limited(client: TestClient, module: MemoryDirectorModule) -> None:
    await _seed(module)
    codes = [
        client.post("/api/auth/login", json={"email": "ana@x.com", "password": "bad"}).status_code
        for _ in range(4)
    ]
    assert codes == [401, 401, 401, 429]


def test_rate_limit_window_slides() -> None:
    now = [0.0]
    limiter = LoginRateLimit(per_minute=2, clock=lambda: now[0])
    assert limiter.check("a") and limiter.check("a") and not limiter.check("a")
    assert limiter.check("b")  # another client is not affected
    now[0] = 61.0
    assert limiter.check("a")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
