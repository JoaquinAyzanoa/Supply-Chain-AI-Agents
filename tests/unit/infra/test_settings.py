"""Tests for sc_core.infra.settings."""

import pytest
from pydantic import ValidationError

from sc_core.infra.settings import Settings, get_settings


def test_defaults_are_valid(clean_env: pytest.MonkeyPatch) -> None:
    """With no environment, defaults parse into typed DSNs and dev mode."""
    s = Settings(_env_file=None)
    assert s.service_name == "unnamed"
    assert s.environment == "dev"
    assert s.is_dev is True
    assert s.use_json_logs is False
    # PostgresDsn is a multi-host URL in pydantic v2; inspect the first host.
    (host,) = s.app_db.dsn.hosts()
    assert host["host"] == "localhost"
    assert host["port"] == 15432
    assert s.redis.dsn.scheme == "redis"


def test_nested_env_override(clean_env: pytest.MonkeyPatch) -> None:
    """SC__<SECTION>__<FIELD> reaches nested sections."""
    clean_env.setenv("SC__SERVICE_NAME", "director")
    clean_env.setenv("SC__ENVIRONMENT", "prod")
    clean_env.setenv("SC__APP_DB__DSN", "postgresql://u:p@db.internal:5432/scai")
    clean_env.setenv("SC__APP_DB__POOL_MAX", "25")
    s = Settings(_env_file=None)
    assert s.service_name == "director"
    assert s.use_json_logs is True
    (host,) = s.app_db.dsn.hosts()
    assert host["host"] == "db.internal"
    assert s.app_db.pool_max == 25


def test_log_json_flag_overrides_environment(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SC__LOG_JSON", "true")
    assert Settings(_env_file=None).use_json_logs is True
    clean_env.setenv("SC__ENVIRONMENT", "prod")
    clean_env.setenv("SC__LOG_JSON", "false")
    assert Settings(_env_file=None).use_json_logs is False


def test_invalid_dsn_fails_at_construction(clean_env: pytest.MonkeyPatch) -> None:
    """A bad DSN must fail loudly at startup, not on first use."""
    clean_env.setenv("SC__APP_DB__DSN", "not-a-dsn")
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    assert "app_db" in str(exc.value)


def test_invalid_enum_values_fail(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SC__ENVIRONMENT", "production")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    clean_env.delenv("SC__ENVIRONMENT")
    clean_env.setenv("SC__LOG_LEVEL", "verbose")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_unknown_top_level_vars_are_ignored(clean_env: pytest.MonkeyPatch) -> None:
    """Extra SC__ variables (from other services) must not break this one."""
    clean_env.setenv("SC__SOMETHING_ELSE", "x")
    Settings(_env_file=None)


def test_unknown_nested_field_is_rejected(clean_env: pytest.MonkeyPatch) -> None:
    """A typo inside a section is an error, so misconfiguration is visible."""
    clean_env.setenv("SC__APP_DB__DNS", "postgresql://u:p@h/db")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_is_cached_until_reset(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SC__SERVICE_NAME", "first")
    first = get_settings()
    clean_env.setenv("SC__SERVICE_NAME", "second")
    assert get_settings() is first
    from sc_core.infra.settings import reset_settings_cache

    reset_settings_cache()
    assert get_settings().service_name == "second"


def test_trace_links_are_rehomed_on_the_browser_url(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from sc_core.infra import tracing

    monkeypatch.setattr(tracing, "_host", "http://localhost:3000")
    assert (
        tracing.browser_trace_url("http://langfuse-web:3000/trace/abc")
        == "http://localhost:3000/trace/abc"
    )
    assert tracing.browser_trace_url(None) is None
    assert tracing.browser_trace_url("https://elsewhere/x") == "https://elsewhere/x"
    monkeypatch.setattr(tracing, "_host", "")
    assert tracing.browser_trace_url("http://langfuse-web:3000/trace/abc") == (
        "http://langfuse-web:3000/trace/abc"
    )
