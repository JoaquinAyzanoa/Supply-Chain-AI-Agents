"""Login, JWT sessions and roles for the Control Tower.

Users live in ``ui_users`` (migration 006) with argon2 password hashes;
``just ui-create-user`` creates them. A login answers a signed JWT
(``SC__UI__JWT_SECRET``, falling back to the events secret) that carries
the user's email, name and role; ``current_user`` verifies it on every
request and ``require_role`` narrows a route to some roles. Phase 10 swaps
the login for Entra ID and keeps the same bearer contract.

Roles: ``viewer`` reads, ``approver`` also resolves approvals and runs
actions, ``admin`` also changes settings and users.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, runtime_checkable

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi_injector import Injected
from loguru import logger
from pydantic import EmailStr, Field

from sc_core.infra.db import Database
from sc_core.infra.settings import Settings
from sc_core.schema.base import StrictModel
from sc_core.shared.time import utc_now

Role = Literal["viewer", "approver", "admin"]
ROLE_RANK: dict[str, int] = {"viewer": 0, "approver": 1, "admin": 2}
ALGORITHM = "HS256"

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)
_hasher = PasswordHasher()


# --- models ------------------------------------------------------------------------------


class User(StrictModel):
    id: int
    email: str
    name: str
    role: Role
    active: bool = True


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(StrictModel):
    token: str
    role: Role
    name: str
    email: str
    expires_at: datetime


class Principal(StrictModel):
    """Who is calling, as decoded from the token."""

    email: str
    name: str
    role: Role

    def at_least(self, role: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


# --- passwords and tokens -------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError:
        return False


def issue_token(user: User, *, secret: str, ttl_minutes: int, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    payload = {
        "sub": user.email,
        "name": user.name,
        "role": user.role,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(minutes=ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, *, secret: str) -> Principal:
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="token expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    try:
        return Principal(email=payload["sub"], name=payload["name"], role=payload["role"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc


def jwt_secret(settings: Settings) -> str:
    secret = (
        settings.ui.jwt_secret.get_secret_value()
        or settings.events.signing_secret.get_secret_value()
    )
    if not secret:
        raise HTTPException(status_code=503, detail="SC__UI__JWT_SECRET is not configured")
    return secret


# --- user store ------------------------------------------------------------------------


@runtime_checkable
class UserStore(Protocol):
    async def by_email(self, email: str) -> tuple[User, str] | None:
        """The user and their password hash, or ``None``."""
        ...

    async def create(self, *, email: str, name: str, password_hash: str, role: Role) -> User: ...

    async def touch_login(self, user_id: int) -> None: ...

    async def list(self) -> list[User]: ...


class PostgresUserStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def by_email(self, email: str) -> tuple[User, str] | None:
        row = await self._db.fetch_one(
            "SELECT id, email, name, role, active, password_hash FROM ui_users "
            "WHERE lower(email) = lower(%s)",
            (email,),
        )
        if row is None:
            return None
        password_hash = str(row.pop("password_hash"))
        return User(**row), password_hash

    async def create(self, *, email: str, name: str, password_hash: str, role: Role) -> User:
        row = await self._db.fetch_one(
            "INSERT INTO ui_users (email, name, password_hash, role) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name, "
            "password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, active = true "
            "RETURNING id, email, name, role, active",
            (email.lower(), name, password_hash, role),
        )
        assert row is not None
        return User(**row)

    async def touch_login(self, user_id: int) -> None:
        await self._db.execute(
            "UPDATE ui_users SET last_login_at = %s WHERE id = %s", (utc_now(), user_id)
        )

    async def list(self) -> list[User]:
        rows = await self._db.fetch_all(
            "SELECT id, email, name, role, active FROM ui_users ORDER BY id"
        )
        return [User(**r) for r in rows]


class MemoryUserStore:
    def __init__(self) -> None:
        self.users: dict[str, tuple[User, str]] = {}
        self.logins: list[int] = []

    async def by_email(self, email: str) -> tuple[User, str] | None:
        return self.users.get(email.lower())

    async def create(self, *, email: str, name: str, password_hash: str, role: Role) -> User:
        user = User(id=len(self.users) + 1, email=email.lower(), name=name, role=role)
        self.users[user.email] = (user, password_hash)
        return user

    async def touch_login(self, user_id: int) -> None:
        self.logins.append(user_id)

    async def list(self) -> list[User]:
        return [u for u, _ in self.users.values()]


# --- rate limit ------------------------------------------------------------------------


class LoginRateLimit:
    """At most ``per_minute`` login attempts per client address, in this process."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = per_minute
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = self._clock()
        hits = self._hits[key]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True


# --- dependencies ----------------------------------------------------------------------


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Injected(Settings),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="missing bearer token")
    return decode_token(credentials.credentials, secret=jwt_secret(settings))


def require_role(role: Role) -> Callable[..., Any]:
    async def check(principal: Principal = Depends(current_user)) -> Principal:
        if not principal.at_least(role):
            raise HTTPException(status_code=403, detail=f"{role} role required")
        return principal

    return check


CurrentUser = Depends(current_user)
Viewer = Depends(require_role("viewer"))
Approver = Depends(require_role("approver"))
Admin = Depends(require_role("admin"))


# --- routes ------------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    settings: Settings = Injected(Settings),
    users: UserStore = Injected(UserStore),  # type: ignore[type-abstract]
    limiter: LoginRateLimit = Injected(LoginRateLimit),
) -> LoginResponse:
    client = request.client.host if request.client else "unknown"
    if not limiter.check(client):
        raise HTTPException(status_code=429, detail="too many login attempts; try again later")
    found = await users.by_email(str(body.email))
    if found is None or not found[0].active or not verify_password(body.password, found[1]):
        logger.bind(email=str(body.email), client=client).warning("login refused")
        raise HTTPException(status_code=401, detail="invalid email or password")
    user, _ = found
    ttl = settings.ui.jwt_ttl_minutes
    token = issue_token(user, secret=jwt_secret(settings), ttl_minutes=ttl)
    await users.touch_login(user.id)
    logger.bind(email=user.email, role=user.role).info("login")
    return LoginResponse(
        token=token,
        role=user.role,
        name=user.name,
        email=user.email,
        expires_at=datetime.now(UTC) + timedelta(minutes=ttl),
    )


@router.get("/me", response_model=Principal)
async def me(principal: Principal = CurrentUser) -> Principal:
    return principal
