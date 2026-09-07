"""Import the ASGI entrypoint of every runnable member.

A member is runnable when it ships ``<name>/main.py``. Importing it builds
the FastAPI app, which exercises settings, dependency injection and router
wiring without starting a server. Used by CI and by ``just smoke``.
"""

from __future__ import annotations

import importlib
import sys

from members import members


def main() -> int:
    failures = 0
    for member in members():
        if not (member / member.name / "main.py").is_file():
            print(f"-- {member.name}: library, skipped")
            continue
        try:
            module = importlib.import_module(f"{member.name}.main")
            app = module.app
            print(f"ok {member.name}: {app.title} v{app.version}")
        except Exception as exc:  # noqa: BLE001 - report every member, then fail
            failures += 1
            print(f"FAIL {member.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
