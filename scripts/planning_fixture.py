"""Regenerate the synthetic weekly demand series used by the forecasting tests.

Writes ``tests/fixtures/planning/series.json``: stable, trending, intermittent,
seasonal and short series from a fixed RNG seed, so the file only changes
when this script does. Usage: ``uv run python scripts/planning_fixture.py``.
"""

from __future__ import annotations

import json
import math
import random

from members import ROOT

OUT = ROOT / "tests" / "fixtures" / "planning" / "series.json"
WEEKS = 104


def main() -> int:
    rng = random.Random(20260914)
    series: dict[str, list[float]] = {
        "stable": [max(0.0, round(rng.gauss(10, 2))) for _ in range(WEEKS)],
        "trending": [max(0.0, round(rng.gauss(8 + 0.12 * i, 1.5))) for i in range(WEEKS)],
        "intermittent": [
            float(rng.choice([2, 3, 4, 6])) if rng.random() < 0.25 else 0.0 for _ in range(WEEKS)
        ],
        "seasonal": [
            max(0.0, round(rng.gauss(12 * (1 + 0.3 * math.sin(2 * math.pi * i / 52)), 2)))
            for i in range(WEEKS)
        ],
        "short": [6.0, 8.0, 7.0, 9.0, 6.0],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(series, indent=0) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({', '.join(f'{k}: {len(v)}' for k, v in series.items())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
