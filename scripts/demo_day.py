"""Run the demo day against the live stack: ``python scripts/demo_day.py --auto``.

The script drives the director's demo API (the same one the Demo page uses), so
the page and the terminal always agree on where the scenario stands. The
supplier side of the conversation is sent from the demo supplier's mailbox by the
director (``SC__DEMO__SMTP_*``); the warehouse and accounting side through the
demo's Odoo login (``SC__DEMO__ODOO_*``). See ``docs/demo.md``.

    --auto     run every step without stopping; decide the approvals as the presenter
    --pace     wait for Enter before each step; the presenter decides in the inbox
    --reset    only put the demo orders back to their start state
    --from N   start at step N (1-based) instead of resetting
    --approve  with --pace: still decide the approvals automatically

Login: ``SC_DEMO_USER`` (default admin@scai.dev) and ``SC_UI_PASSWORD``; the
director at ``SC_DIRECTOR_URL`` (default http://localhost:8010).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx

DEFAULT_URL = "http://localhost:8010"
DEFAULT_USER = "admin@scai.dev"
RETRIES_PER_STEP = 6  # a waiting step is asked again this many times before giving up


def _client(base: str, user: str, password: str) -> httpx.Client:
    client = httpx.Client(base_url=base.rstrip("/"), timeout=600)
    response = None
    for _ in range(10):  # a director just restarted answers after a few seconds
        try:
            response = client.post("/api/auth/login", json={"email": user, "password": password})
            break
        except httpx.HTTPError as exc:
            print(f"director not answering yet ({exc.__class__.__name__}), retrying")
            time.sleep(3)
    if response is None:
        raise SystemExit(f"the director at {base} did not answer")
    if response.status_code != 200:
        raise SystemExit(f"login failed for {user}: {response.status_code} {response.text[:200]}")
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


def _print_view(view: dict[str, Any]) -> None:
    steps = view["steps"]
    print(f"\n== demo: {view['position']}/{len(steps)} steps done ==")
    for note in view["ready"].get("notes") or []:
        print(f"  ! {note}")
    outcomes = {o["key"]: o for o in view["outcomes"]}
    for index, step in enumerate(steps, start=1):
        outcome = outcomes.get(step["key"])
        mark = {"done": "x", "waiting": "~", "failed": "!"}.get(
            outcome["status"] if outcome else "", " "
        )
        current = ">" if view.get("next") and view["next"]["key"] == step["key"] else " "
        print(f"{current}[{mark}] {index}. {step['title']}")
        if outcome:
            print(f"       {outcome['summary']}")


def _say(step: dict[str, Any]) -> None:
    print(f"\n--- {step['title']}")
    print(f"say:  {step['say']}")
    print(f"look: {step['click']}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--auto", action="store_true", help="run unattended, deciding approvals")
    mode.add_argument("--pace", action="store_true", help="wait for Enter before each step")
    parser.add_argument("--reset", action="store_true", help="only reset the demo orders")
    parser.add_argument("--from", dest="start", type=int, default=0, help="start at this step")
    parser.add_argument("--approve", action="store_true", help="with --pace: decide approvals")
    parser.add_argument("--url", default=os.environ.get("SC_DIRECTOR_URL", DEFAULT_URL))
    parser.add_argument("--user", default=os.environ.get("SC_DEMO_USER", DEFAULT_USER))
    args = parser.parse_args(argv)
    password = os.environ.get("SC_UI_PASSWORD")
    if not password:
        print("SC_UI_PASSWORD not set", file=sys.stderr)
        return 2
    if not (args.auto or args.pace or args.reset):
        parser.error("choose --auto, --pace or --reset")
    client = _client(args.url, args.user, password)
    approve = args.auto or args.approve

    started = time.monotonic()
    if args.start:
        view = client.get("/api/demo").json()
        if not view.get("started_at"):
            print("the demo was never reset; resetting first")
            view = client.post("/api/demo/reset").json()
    else:
        view = client.post("/api/demo/reset").json()
        print("reset: " + ("; ".join(view["records"].get("reset_notes") or []) or "clean"))
    _print_view(view)
    if args.reset:
        return 0

    steps = view["steps"]
    for index, step in enumerate(steps, start=1):
        if index < max(args.start, 1):
            continue
        _say(step)
        if args.pace:
            try:
                input("press Enter to run this step (Ctrl+C to stop) ")
            except (EOFError, KeyboardInterrupt):
                print("\nstopped; the Demo page keeps the position")
                return 130
        outcome: dict[str, Any] | None = None
        for attempt in range(RETRIES_PER_STEP):
            response = client.post("/api/demo/next", json={"approve": approve, "step": step["key"]})
            if response.status_code != 200:
                print(f"step {index} refused: {response.status_code} {response.text[:300]}")
                return 1
            view = response.json()
            outcome = next((o for o in view["outcomes"] if o["key"] == step["key"]), None)
            if outcome is None or outcome["status"] != "waiting":
                break
            print(f"  ~ {outcome['summary']} (attempt {attempt + 1}/{RETRIES_PER_STEP})")
        assert outcome is not None
        mark = {"done": "ok", "waiting": "still waiting", "failed": "FAILED"}[outcome["status"]]
        print(f"  {mark}: {outcome['summary']}")
        for link in outcome.get("links") or []:
            print(f"    -> {link['label']}: {link['path']}")
        if outcome["status"] != "done":
            print("the scenario stopped here; fix the cause and run `--from` this step")
            _print_view(view)
            return 1
        elapsed = int(time.monotonic() - started)
        print(f"  ({elapsed // 60}m{elapsed % 60:02d}s into the demo)")
    _print_view(view)
    elapsed = int(time.monotonic() - started)
    print(f"\ndone in {elapsed // 60}m{elapsed % 60:02d}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
