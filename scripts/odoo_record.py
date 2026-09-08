"""Re-record the Odoo cassettes under tests/fixtures/odoo from the live container.

Runs the cassette tests with ``SC_ODOO_RECORD=1`` so every request they make
is answered by the compose Odoo and written to the fixture files. Requires
``just up``, an initialised database and ``SC__ODOO__API_KEY`` in ``.env``.
"""

from __future__ import annotations

import os
import subprocess
import sys

from members import ROOT

CASSETTE_TESTS = ROOT / "tests" / "unit" / "odoo" / "test_cassettes.py"


def main() -> int:
    env = {**os.environ, "SC_ODOO_RECORD": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(CASSETTE_TESTS), "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if result.returncode == 0:
        print("cassettes refreshed under tests/fixtures/odoo; review the diff and commit")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
