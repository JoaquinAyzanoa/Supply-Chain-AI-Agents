"""Re-record LLM cassettes under tests/fixtures/llm from the real provider.

Runs the cassette-marked tests with ``SC__LLM__RECORD_MODE=record`` so every
model call is answered by the configured provider and written to the
fixture files. Requires the provider key in ``.env`` (``DEEPSEEK_API_KEY``).
"""

from __future__ import annotations

import os
import subprocess
import sys

from members import ROOT


def main() -> int:
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
