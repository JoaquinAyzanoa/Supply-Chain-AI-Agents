"""Discover workspace members under src/ (shared by the other scripts)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def members() -> list[Path]:
    """Directories under src/ that contain a pyproject.toml, sorted by name."""
    return sorted(p for p in SRC.iterdir() if (p / "pyproject.toml").is_file())
