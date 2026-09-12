"""Create or update a Control Tower user (idempotent on the email).

    uv run python scripts/ui_create_user.py --email ana@example.com --name "Ana" --role approver

The password comes from ``--password`` or, better, the ``SC_UI_PASSWORD``
environment variable so it never lands in the shell history. Roles:
viewer, approver, admin. Runs against the app database in ``.env``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from director.api.auth import PostgresUserStore, hash_password
from sc_core.infra.db import Database
from sc_core.infra.settings import Settings


async def create(email: str, name: str, role: str, password: str) -> None:
    db = Database(Settings().app_db)
    await db.open()
    try:
        user = await PostgresUserStore(db).create(
            email=email,
            name=name,
            password_hash=hash_password(password),
            role=role,  # type: ignore[arg-type]
        )
        print(f"user {user.email} ({user.role}) ready, id {user.id}")
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", choices=["viewer", "approver", "admin"], default="approver")
    parser.add_argument("--password", default=None, help="or set SC_UI_PASSWORD")
    args = parser.parse_args(argv)
    password = args.password or os.environ.get("SC_UI_PASSWORD")
    if not password or len(password) < 8:
        raise SystemExit("a password of at least 8 characters is required (SC_UI_PASSWORD)")
    asyncio.run(create(args.email, args.name, args.role, password))
    return 0


if __name__ == "__main__":
    sys.exit(main())
