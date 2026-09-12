"""Point the Odoo addon at the orchestrator (idempotent).

Sets the two system parameters ``sc_agents.director_url`` and
``sc_agents.events_secret`` that ``sc.event.emitter`` reads, using the
events secret from ``.env`` and the compose-internal director address.

Usage: ``just odoo-configure`` (or ``uv run python scripts/odoo_configure.py
[--director-url http://director:8000]``). Runs as the Odoo admin: system
parameters are not writable by the bot user.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import SecretStr

from sc_core.infra.settings import Settings
from sc_core.odoo.client import OdooClient

PARAM_URL = "sc_agents.director_url"
PARAM_SECRET = "sc_agents.events_secret"


async def configure(settings: Settings, director_url: str, *, password: str) -> None:
    secret = settings.events.signing_secret.get_secret_value()
    if not secret:
        raise SystemExit("SC__EVENTS__SIGNING_SECRET is not set in .env")
    cfg = settings.odoo.model_copy(update={"login": "admin", "api_key": SecretStr(password)})
    async with OdooClient(cfg) as odoo:
        for key, value in ((PARAM_URL, director_url.rstrip("/")), (PARAM_SECRET, secret)):
            await odoo.call_model("ir.config_parameter", "set_param", key, value)
            shown = value if key == PARAM_URL else "*" * 8
            print(f"{key} = {shown}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--director-url",
        default="http://director:8000",
        help="where Odoo reaches the director (inside compose: http://director:8000)",
    )
    parser.add_argument("--admin-password", default="admin")
    args = parser.parse_args(argv)
    asyncio.run(configure(Settings(), args.director_url, password=args.admin_password))
    return 0


if __name__ == "__main__":
    sys.exit(main())
