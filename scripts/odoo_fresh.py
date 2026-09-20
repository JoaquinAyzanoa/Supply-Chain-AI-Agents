"""A fresh local stack: Odoo without Odoo's demo data, an empty app database, our dataset.

    uv run python scripts/odoo_fresh.py [--yes] [--keep-app-db] [--ui-admin EMAIL] [--from N]

Destructive. It drops the Odoo database and filestore and the application
database (cases, approvals, planning runs, scores, checkpoints), flushes
Redis, then rebuilds everything in order:

1. Odoo installed with ``--without-demo=all`` (no furniture, no demo partners)
2. app database migrated
3. bot API key generated into ``.env``; addon pointed at the director
4. the Sun Hydraulics dataset seeded (``odoo/demo/sun_hydraulics.yaml``)
5. the demo supplier script (no-op when the seed made the partner and an RFQ)
6. the clean check; the Control Tower admin user (``SC_UI_PASSWORD`` required)
7. every agent recreated so it reads the new API key, then day one's receipts
   and bills announced to the director (the logistics and invoice agents run)

The cached mailbox login (``mail_token_cache``) is saved before the app
database is dropped and restored after it is migrated, so ``mail_sync`` keeps
working; if that fails, run ``just mail-login`` again. ``--from N`` resumes
at step N after a failure.

Afterwards: ``just odoo-record`` and ``just llm-record`` to refresh the test
cassettes. Takes a while: the history is two years of orders.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from members import ROOT

COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra" / "docker-compose.yml")]
ODOO_DB = "scai"
# Same list as ODOO_MODULES in the Justfile; keep both in step.
ODOO_MODULES = (
    "base,contacts,mail,product,purchase,stock,purchase_stock,sale_management,"
    "purchase_requisition,base_automation,account,sc_agents"
)
AGENTS = [
    "director",
    "mail_sync",
    "scheduler",
    "supplier_comms",
    "inventory_planning",
    "logistics",
    "invoice_match",
    "supplier_performance",
    "sourcing",  # it reads the bot API key too: left out, its Odoo calls answer "Access Denied"
]


def run(cmd: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> int:
    print("+", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=ROOT, check=False, env=env)
    if check and result.returncode != 0:
        raise SystemExit(f"step failed ({result.returncode}): {' '.join(cmd)}")
    return result.returncode


def script(name: str, *args: str, env: dict[str, str] | None = None) -> None:
    run([sys.executable, str(ROOT / "scripts" / name), *args], env=env)


def wait_http(url: str, *, seconds: int) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                if response.status < 500:
                    return
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(3)
    raise SystemExit(f"{url} did not answer within {seconds}s")


def step(title: str) -> None:
    print(f"\n=== {title}", flush=True)


TOKEN_DUMP = ROOT / ".mail_token_cache.sql"  # git-ignored; deleted after the restore


def save_mail_login() -> bool:
    """Dump the cached mailbox login so the reset does not force a new device-code login."""
    with TOKEN_DUMP.open("wb") as out:
        result = subprocess.run(
            [
                *COMPOSE,
                "exec",
                "-T",
                "app-db",
                "pg_dump",
                "-U",
                "app",
                "--data-only",
                "-t",
                "mail_token_cache",
                "app",
            ],
            cwd=ROOT,
            check=False,
            stdout=out,
        )
    if result.returncode != 0 or TOKEN_DUMP.stat().st_size == 0:
        TOKEN_DUMP.unlink(missing_ok=True)
        print("no cached mailbox login to keep (run `just mail-login` afterwards)")
        return False
    print("cached mailbox login saved")
    return True


def restore_mail_login() -> None:
    if not TOKEN_DUMP.exists():
        return
    with TOKEN_DUMP.open("rb") as dump:
        result = subprocess.run(
            [*COMPOSE, "exec", "-T", "app-db", "psql", "-U", "app", "-q", "app"],
            cwd=ROOT,
            check=False,
            stdin=dump,
        )
    TOKEN_DUMP.unlink(missing_ok=True)
    print(
        "cached mailbox login restored"
        if result.returncode == 0
        else "mailbox login NOT restored: run `just mail-login`"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--keep-app-db", action="store_true", help="keep cases, approvals, users")
    parser.add_argument("--ui-admin", default="admin@scai.dev", help="Control Tower admin email")
    parser.add_argument("--ui-name", default="Admin")
    parser.add_argument("--from", dest="start", type=int, default=1, help="resume at step N")
    args = parser.parse_args()

    if not args.yes and args.start <= 1:
        print(
            "This drops the local Odoo database and filestore"
            + (
                ""
                if args.keep_app_db
                else " AND the application database (cases, approvals, users)"
            )
        )
        answer = input("Type 'fresh' to continue: ").strip()
        if answer != "fresh":
            print("aborted")
            return 1
    started = time.perf_counter()

    if args.start <= 1:
        step("1/7 drop Odoo and the app database")
        run([*COMPOSE, "rm", "-sfv", "odoo", "odoo-db"], check=False)
        for volume in ("scai_odoo-db-data", "scai_odoo-web-data"):
            run(["docker", "volume", "rm", volume], check=False)
        if not args.keep_app_db:
            run([*COMPOSE, "up", "-d", "app-db"], check=False)
            save_mail_login()
            run([*COMPOSE, "rm", "-sfv", "app-db", *AGENTS], check=False)
            run(["docker", "volume", "rm", "scai_app-db-data"], check=False)

    if args.start <= 2:
        step("2/7 install Odoo without demo data")
        run([*COMPOSE, "up", "-d", "odoo-db"])
        run(
            [
                *COMPOSE,
                "run",
                "--rm",
                "odoo",
                "odoo",
                "-d",
                ODOO_DB,
                "-i",
                ODOO_MODULES,
                "--without-demo=all",
                "--stop-after-init",
            ]
        )
    # Only what the next steps need; the agents come last, once the API key exists.
    run([*COMPOSE, "up", "-d", "odoo", "app-db", "redis"])
    wait_http("http://localhost:8069/web/login", seconds=180)

    if args.start <= 3:
        step("3/7 migrate the app database and flush Redis")
        run([sys.executable, "-m", "sc_core.infra.migrate"])
        restore_mail_login()
        run([*COMPOSE, "exec", "-T", "redis", "redis-cli", "FLUSHALL"], check=False)

    if args.start <= 4:
        step("4/7 bot API key and addon configuration")
        script("odoo_apikey.py", "--login", "sc_agent_bot", "--db", ODOO_DB)
        script("odoo_configure.py")

    if args.start <= 5:
        step("5/7 seed the dataset")
        script("odoo_seed.py")
        script("odoo_demo_supplier.py")

    step("6/7 clean check and Control Tower admin")
    script("odoo_check_clean.py")
    if args.keep_app_db:
        print("app database kept: users untouched")
    elif os.environ.get("SC_UI_PASSWORD"):
        script(
            "ui_create_user.py",
            "--email",
            args.ui_admin,
            "--name",
            args.ui_name,
            "--role",
            "admin",
        )
    else:
        print("SC_UI_PASSWORD not set: create the admin later with `just ui-create-user`")

    step("7/7 recreate the agents so they read the new API key")
    run([*COMPOSE, "up", "-d", "--force-recreate", "--no-deps", *AGENTS])
    wait_http("http://localhost:8010/health/ready", seconds=600)
    for port in (8015, 8016):  # logistics, invoice_match: they act on the announcement
        wait_http(f"http://localhost:{port}/health/ready", seconds=300)
    script("odoo_seed.py", "--only", "announce", "--no-summary")
    print("if mail_sync stays unhealthy, the mailbox login is gone: run `just mail-login`")

    minutes = (time.perf_counter() - started) / 60
    print(f"\nfresh stack ready in {minutes:.0f} min")
    print("next: `just odoo-record` and `just llm-record` to refresh the test cassettes")
    return 0


if __name__ == "__main__":
    os.chdir(Path(ROOT))
    raise SystemExit(main())
