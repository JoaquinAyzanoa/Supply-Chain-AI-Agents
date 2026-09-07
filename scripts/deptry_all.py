"""Run deptry once per workspace member.

deptry reads the pyproject.toml of the directory it runs in, so a single run
from the repository root would check every import against the root project,
which has no dependencies. Running per member checks each package against
what it actually declares.
"""

from __future__ import annotations

import subprocess
import sys

from members import members


def main() -> int:
    failed: list[str] = []
    for member in members():
        print(f"== deptry {member.name}", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "deptry", ".", "--config", "pyproject.toml"],
            cwd=member,
            check=False,
        )
        if result.returncode != 0:
            failed.append(member.name)
    if failed:
        print(f"deptry failed for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
