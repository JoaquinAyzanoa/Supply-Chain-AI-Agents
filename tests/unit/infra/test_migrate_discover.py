"""Tests for migration discovery (no database needed)."""

from pathlib import Path

import pytest

from sc_core.infra.migrate import discover
from sc_core.shared.errors import ConfigurationError


def _touch(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_text("SELECT 1;", encoding="utf-8")


def test_sorted_by_version(tmp_path: Path) -> None:
    _touch(tmp_path, "010_third.sql", "002_second.sql", "001_first.sql", "notes.txt")
    versions = [m.version for m in discover(tmp_path)]
    assert versions == ["001", "002", "010"]
    assert discover(tmp_path)[0].name == "001_first"


def test_bad_name_rejected(tmp_path: Path) -> None:
    _touch(tmp_path, "1_first.sql")
    with pytest.raises(ConfigurationError, match="bad migration file name"):
        discover(tmp_path)
    (tmp_path / "1_first.sql").unlink()
    _touch(tmp_path, "001-First.sql")
    with pytest.raises(ConfigurationError):
        discover(tmp_path)


def test_duplicate_version_rejected(tmp_path: Path) -> None:
    _touch(tmp_path, "001_a.sql", "001_b.sql")
    with pytest.raises(ConfigurationError, match="duplicate migration version 001"):
        discover(tmp_path)


def test_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        discover(tmp_path / "nope")


def test_repo_migrations_directory_is_valid() -> None:
    root = Path(__file__).resolve().parents[3] / "migrations"
    discover(root)  # must not raise, even when empty
