"""Make a fresh stack ready for the demo: ``python scripts/demo_prepare.py``.

After ``just odoo-fresh`` the desk is empty: no supplier scores, no daily plan, no risk radar.
This fires the two jobs that fill it (supplier scorecards, the daily replenishment plan),
waits for their approvals to arrive, approves the scorecards (so suppliers have a score for
comparisons) and leaves the plan for a person, as on any first morning. Then it says whether
the demo is ready. Login: ``SC_DEMO_USER`` (default admin@scai.dev) and ``SC_UI_PASSWORD``.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

DIRECTOR = os.environ.get("SC_DIRECTOR_URL", "http://localhost:8010").rstrip("/")
SCHEDULER = os.environ.get("SC_SCHEDULER_URL", "http://localhost:8012").rstrip("/")
USER = os.environ.get("SC_DEMO_USER", "admin@scai.dev")
WAIT_SECONDS = 600


def call(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One request with a few retries: a busy local stack drops a connection now and then."""
    last: Exception | None = None
    for _ in range(6):
        try:
            return httpx.request(method, url, timeout=180, **kwargs)
        except httpx.HTTPError as exc:
            last = exc
            time.sleep(3)
    raise SystemExit(f"{url} did not answer: {last}")


def main() -> int:
    password = os.environ.get("SC_UI_PASSWORD")
    if not password:
        print("SC_UI_PASSWORD not set", file=sys.stderr)
        return 2
    login = call("POST", f"{DIRECTOR}/api/auth/login", json={"email": USER, "password": password})
    if login.status_code != 200:
        print(f"login failed for {USER}: {login.status_code}", file=sys.stderr)
        return 1
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    for job in ("supplier_performance", "inventory_planning"):
        run = call("POST", f"{SCHEDULER}/jobs/{job}/run-now")
        print(f"{job}: {run.json().get('status', run.status_code)}")

    print("waiting for the scorecards and the daily plan", end="", flush=True)
    deadline = time.monotonic() + WAIT_SECONDS
    pending: dict[str, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        rows = call(
            "GET", f"{DIRECTOR}/api/approvals", params={"status": "pending"}, headers=headers
        ).json()
        pending = {row["kind"]: row for row in rows}
        if "supplier_score" in pending and "planning_run" in pending:
            break
        print(".", end="", flush=True)
        time.sleep(10)
    print()
    if "supplier_score" in pending:
        done = call(
            "POST",
            f"{DIRECTOR}/api/approvals/{pending['supplier_score']['id']}/resolve",
            json={"status": "approved", "reason": "demo preparation"},
            headers=headers,
        )
        print(f"scorecards approved: {done.status_code == 200}")
        time.sleep(5)
    else:
        print("no scorecards arrived: is the performance agent up?")

    scores = call("GET", f"{DIRECTOR}/api/performance/scores", headers=headers).json()
    for score in scores:
        print(f"  {score['partner_name']}: {score['score']:.0f} points, OTIF {score['otif']:.0%}")
    risk = call("GET", f"{DIRECTOR}/api/risk", headers=headers).json()
    print(f"risk radar: {len(risk.get('products', []))} product(s)")
    ready = call("GET", f"{DIRECTOR}/api/demo", headers=headers).json()["ready"]
    for note in ready.get("notes") or []:
        print(f"  ! {note}")
    ok = bool(scores) and bool(risk.get("products")) and not ready.get("notes")
    print("the demo is ready" if ok else "the demo is NOT ready")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
