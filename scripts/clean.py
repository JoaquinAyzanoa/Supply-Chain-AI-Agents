"""Remove build, test and cache artifacts. Never touches .venv or .env."""

from __future__ import annotations

import shutil

from members import ROOT

DIRS = ("dist", "build", "htmlcov", ".pytest_cache", ".mypy_cache", ".ruff_cache")
FILES = (".coverage", "coverage.xml")
GLOBS = ("**/__pycache__", "**/*.egg-info")


def main() -> int:
    removed = 0
    for name in DIRS:
        path = ROOT / name
        if path.is_dir():
            shutil.rmtree(path)
            removed += 1
    for name in FILES:
        path = ROOT / name
        if path.is_file():
            path.unlink()
            removed += 1
    for pattern in GLOBS:
        for path in ROOT.glob(pattern):
            if ".venv" in path.parts:
                continue
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            removed += 1
    print(f"removed {removed} artifact(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
