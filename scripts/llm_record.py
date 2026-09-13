"""Re-record LLM cassettes under tests/fixtures/llm from the real provider.

Clears the fixture files, then runs the cassette-marked tests with
``SC__LLM__RECORD_MODE=record`` so every model call is answered by the
configured provider and written afresh (append-only cassettes would replay an
old answer against new tool output). Requires the provider key in ``.env`` (``DEEPSEEK_API_KEY``).
"""

from __future__ import annotations

import os
import subprocess
import sys

from members import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "llm"


def main() -> int:
    # Recording appends, and a request that did not change keeps its old key: left in
    # place, an old answer would be replayed against new tool output. Start clean.
    for stale in sorted(FIXTURES.glob("*.json")):
        stale.unlink()
        print(f"cleared {stale.relative_to(ROOT)}")
    env = {**os.environ, "SC__LLM__RECORD_MODE": "record"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit",
            "-q",
            "-m",
            "llm_cassette",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if result.returncode == 0:
        print("LLM cassettes refreshed under tests/fixtures/llm; review the diff and commit")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
