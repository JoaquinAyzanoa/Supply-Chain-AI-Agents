"""Application settings.

All configuration enters the system here, through environment variables with
the ``SC__`` prefix and ``__`` as the nesting delimiter, for example
``SC__APP_DB__DSN=postgresql://...``. A ``.env`` file in the working directory
is read in development. Nothing else in the codebase should call
``os.environ`` for configuration.

Later phases add one sub-model per concern (``odoo``, ``mail``, ``llm``,
``langfuse``) so that validation happens once, at startup.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "staging", "prod"]
LogLevel = Literal["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]


class _Section(BaseModel):
    """Base for nested settings sections.

    ``validate_default=True`` makes pydantic parse default strings into their
    declared types (``PostgresDsn``, ``RedisDsn``), so a default is validated
    exactly like an environment value.
    """

    model_config = ConfigDict(validate_default=True, extra="forbid")


class AppDbCfg(_Section):
    """Connection to the application database (checkpoints, cases, jobs)."""

    dsn: PostgresDsn = Field(default="postgresql://app:app@localhost:5433/app")  # type: ignore[assignment]
    pool_min: int = Field(default=1, ge=0)
    pool_max: int = Field(default=10, ge=1)


class RedisCfg(_Section):
    """Redis is used for locks and pub/sub only; nothing durable lives there."""

    dsn: RedisDsn = Field(default="redis://localhost:6379/0")  # type: ignore[assignment]


class HttpCfg(_Section):
    """HTTP server and middleware settings shared by every service."""

    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    max_request_bytes: int = Field(default=1_048_576, ge=1)  # 1 MiB: JSON events and webhooks
    enable_hsts: bool = False  # only behind TLS (phase 10)
    access_log: bool = False  # request logging is done by our middleware, not uvicorn


class Settings(BaseSettings):
    """Root settings object.

    Every service constructs exactly one instance (see :func:`get_settings`)
    and passes it explicitly to the components that need it.
    """

    model_config = SettingsConfigDict(
        env_prefix="SC__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = Field(default="unnamed", min_length=1)
    environment: Environment = "dev"
    log_level: LogLevel = "INFO"
    log_json: bool | None = Field(
        default=None,
        description="Force JSON logs on/off. When unset, JSON is used outside 'dev'.",
    )
    timezone: str = "America/Lima"

    http: HttpCfg = Field(default_factory=HttpCfg)
    app_db: AppDbCfg = Field(default_factory=AppDbCfg)
    redis: RedisCfg = Field(default_factory=RedisCfg)

    @property
    def is_dev(self) -> bool:
        return self.environment == "dev"

    @property
    def use_json_logs(self) -> bool:
        return self.log_json if self.log_json is not None else not self.is_dev


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, built once from the environment."""
    return Settings()


def reset_settings_cache() -> None:
    """Forget the cached settings. Intended for tests that mutate the environment."""
    get_settings.cache_clear()
