"""Write the director's OpenAPI document for the Control Tower's generated client.

``just ui-openapi`` runs this, then ``openapi-typescript`` turns the JSON into
``src/api/schema.d.ts``. Both files are committed; CI regenerates them and
fails on a diff, so the frontend never drifts from the API it was built on.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

OUT = Path("frontend/control-tower/openapi.json")


def main() -> int:
    # Import lazily: the director builds its settings from the environment at import time.
    os.environ.setdefault("SC__ENVIRONMENT", "test")
    from director.main import build_app

    spec = build_app().openapi()
    # Only the Control Tower routes: the events endpoint and the system routes are not
    # part of the browser's contract.
    spec["paths"] = {p: v for p, v in spec["paths"].items() if p.startswith("/api/")}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if OUT.exists() and OUT.read_text(encoding="utf-8") == text:
        print(f"{OUT} unchanged ({len(spec['paths'])} paths)")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(spec['paths'])} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
