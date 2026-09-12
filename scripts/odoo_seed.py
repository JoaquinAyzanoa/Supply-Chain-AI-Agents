"""Load the Sun Hydraulics demo dataset into the local Odoo (idempotent).

    python scripts/odoo_seed.py [--only catalogue|stock|demand|supply] [--months N] [--seed N] [--dry-run]

Runs as the administrator (password "admin" on the demo database, override
with --password). Every record is looked up by its natural key first, so a
second run creates nothing. See odoo/demo/README.md for what is generated.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src" / "sc_core"))

from odoo_seed_lib import catalogue, partners  # noqa: E402
from odoo_seed_lib.client import SeedClient  # noqa: E402
from odoo_seed_lib.dataset import DEFAULT_PATH, load  # noqa: E402
from sc_core.infra.settings import Settings  # noqa: E402

STAGES = ("catalogue", "stock", "demand", "supply")


async def run(args: argparse.Namespace) -> int:
    ds = load(Path(args.file) if args.file else DEFAULT_PATH)
    if args.months:
        ds = ds.model_copy(update={"history": ds.history.model_copy(update={"months": args.months})})
    if args.seed is not None:
        ds = ds.model_copy(update={"seed": args.seed})
    stages = [args.only] if args.only else list(STAGES)
    print(f"dataset: {len(ds.products)} products, {len(ds.suppliers)} suppliers, "
          f"{len(ds.customers)} customers, {ds.history.months} months, seed {ds.seed}")
    if args.dry_run:
        print("dry run: nothing written")
        return 0

    settings = Settings()
    started = time.perf_counter()
    async with SeedClient.from_settings(settings, password=args.password) as odoo:
        client = SeedClient(odoo)
        await catalogue.ensure_language(client, ds.language)
        await catalogue.ensure_company(client, ds)
        categories = await catalogue.ensure_categories(client, ds)
        products = await catalogue.ensure_products(client, ds, categories)
        suppliers = await partners.ensure_suppliers(client, ds)
        rows = await partners.ensure_supplierinfo(client, ds, suppliers, products)
        customers = await partners.ensure_customers(client, ds)
        print(f"catalogue: {len(products)} products, {rows} supplier terms, "
              f"{len(customers)} customers")
        if any(s in stages for s in ("stock", "demand", "supply")):
            from odoo_seed_lib import history

            await history.run(client, ds, products, suppliers, customers, stages=stages)
        print("records:")
        print(client.summary())
    print(f"done in {time.perf_counter() - started:.0f}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", choices=STAGES)
    parser.add_argument("--months", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--file")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--dry-run", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
