"""Regenerate the A2A contract snapshots (tests/fixtures/schemas) on purpose.

Runs the snapshot test with SC_SCHEMA_UPDATE=1 so the fixtures are rewritten
from the current models. Review the diff before committing: a changed
snapshot is a contract change for the director and the agents.
"""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    env = {**os.environ, "SC_SCHEMA_UPDATE": "1"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/schema/test_a2a_contracts.py",
            "-q",
            "-k",
            "snapshot",
        ],
        env=env,
        check=False,
    )
    if result.returncode == 0:
        print("schema snapshots refreshed under tests/fixtures/schemas; review the diff and commit")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
