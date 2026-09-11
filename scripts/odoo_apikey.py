"""Generate an Odoo API key for the bot user and store it in .env.

Odoo exposes API-key creation only through a user's own Preferences page,
which needs an interactive login. The same internal routine is available
from ``odoo shell`` inside the container, so this script runs it there and
writes the result to ``SC__ODOO__API_KEY`` in the repository's ``.env``.

Usage: ``just odoo-apikey`` (or ``uv run python scripts/odoo_apikey.py [--login sc_agent_bot]``).
Each run creates a new key; old keys stay valid until revoked in Odoo.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

from members import ROOT

COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra" / "docker-compose.yml")]
MARKER = "SCAI_API_KEY="

# Runs inside `odoo shell`: `env` is provided by the shell. The key is
# generated on behalf of the bot so it belongs to that user.
SNIPPET = """
from datetime import datetime, timedelta
user = env["res.users"].search([("login", "=", {login!r})], limit=1)
assert user, "user {login} not found; is sc_agents installed?"
# Odoo 18 requires an expiration date on every API key.
expires = datetime.now() + timedelta(days={days})
key = env["res.users.apikeys"].with_user(user)._generate("rpc", "sc_core ({login})", expires)
env.cr.commit()
print("{marker}" + key)
"""


def generate(login: str, db: str, days: int) -> str:
    code = SNIPPET.format(login=login, marker=MARKER, days=days)
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "odoo", "odoo", "shell", "-d", db, "--no-http"],
        input=code,
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(rf"{MARKER}(\S+)", result.stdout + result.stderr)
    if not match:
        sys.stderr.write(result.stdout[-2000:] + result.stderr[-2000:])
        raise SystemExit(
            "could not generate the API key (is `just up` running and the db initialised?)"
        )
    return match.group(1)


def write_env(key: str) -> None:
    env_path = ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    pattern = re.compile(r"^#?\s*SC__ODOO__API_KEY=.*$")
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"SC__ODOO__API_KEY={key}"
            replaced = True
            break
    if not replaced:
        lines.append(f"SC__ODOO__API_KEY={key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--login", default="sc_agent_bot")
    parser.add_argument("--db", default="scai")
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="validity in days; Odoo caps it (90 by default, parameter base.api_key_duration)",
    )
    args = parser.parse_args()
    key = generate(args.login, args.db, args.days)
    write_env(key)
    print(
        f"API key for {args.login} written to .env as SC__ODOO__API_KEY "
        f"({key[:6]}..., valid {args.days} days)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
