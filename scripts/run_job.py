"""Trigger a scheduler job by hand: ``python scripts/run_job.py mail_sync``.

Talks to the scheduler's ``POST /jobs/{id}/run-now`` (host port 8012 by
default, override with SC_SCHEDULER_URL) and prints the run record.
"""

from __future__ import annotations

import json
import os
import sys

import httpx


def main(argv: list[str]) -> int:
    job = argv[0] if argv else "mail_sync"
    base = os.environ.get("SC_SCHEDULER_URL", "http://localhost:8012").rstrip("/")
    try:
        response = httpx.post(f"{base}/jobs/{job}/run-now", timeout=630)
    except httpx.HTTPError as exc:
        print(f"scheduler not reachable at {base}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(response.json(), indent=2))
    if response.status_code != 200:
        return 1
    return 0 if response.json().get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
