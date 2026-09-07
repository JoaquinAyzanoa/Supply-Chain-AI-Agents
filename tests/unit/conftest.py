"""Shared fixtures for unit tests."""

from collections.abc import Iterator

import pytest

from sc_core.infra.settings import reset_settings_cache


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Remove every SC__ variable and forget cached settings.

    Tests that need specific values set them on the returned monkeypatch.
    The cache is reset again on teardown so the next test starts clean.
    """
    import os

    for key in list(os.environ):
        if key.startswith("SC__"):
            monkeypatch.delenv(key, raising=False)
    reset_settings_cache()
    yield monkeypatch
    reset_settings_cache()
