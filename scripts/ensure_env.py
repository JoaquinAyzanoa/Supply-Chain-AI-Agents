"""Create .env from .env.example unless it already exists."""

from __future__ import annotations

import shutil

from members import ROOT


def main() -> int:
    target = ROOT / ".env"
    if target.exists():
        print(".env already exists, leaving it untouched")
        return 0
    shutil.copyfile(ROOT / ".env.example", target)
    print("created .env from .env.example; edit it for your machine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
