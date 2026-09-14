"""Environment-based configuration for the ADG backend.

Configuration is read from the process environment (and from a local ``.env`` file
during development). No secret value is ever committed to source control; see
``.env.example`` at the repository root for the documented variables.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LogFormat = Literal["json", "text"]

# Development-only default. Production deployments must supply ADG_DATABASE_URL;
# `Settings` refuses to start in production while this value is still in place.
DEV_DATABASE_URL = "postgresql+psycopg://adg:adg_dev_password@localhost:5432/adg"

SUPPORTED_DB_SCHEME = "postgresql+psycopg"


class Settings(BaseSettings):
    """Runtime configuration for the ADG backend."""

    model_config = SettingsConfigDict(
        env_prefix="ADG_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ADG"
    environment: Environment = "development"
    log_level: str = "INFO"
    log_format: LogFormat = "json"

    database_url: str = DEV_DATABASE_URL
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    database_pool_size: int = Field(default=5, ge=1, le=50)

    # Comma-separated list of browser origins allowed to call the API.
    cors_allow_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed:
            raise ValueError(f"ADG_LOG_LEVEL must be one of {sorted(allowed)}; received {value!r}.")
        return normalized

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value.startswith(f"{SUPPORTED_DB_SCHEME}://"):
            raise ValueError(
                "ADG_DATABASE_URL must use the "
                f"{SUPPORTED_DB_SCHEME!r} scheme (PostgreSQL via psycopg); "
                f"received {value.split('://', 1)[0]!r}."
            )
        return value

    @model_validator(mode="after")
    def _reject_development_defaults_in_production(self) -> Settings:
        if self.is_production and self.database_url == DEV_DATABASE_URL:
            raise ValueError(
                "ADG_DATABASE_URL is still the development default while "
                "ADG_ENVIRONMENT=production. Set ADG_DATABASE_URL to the production "
                "connection string before starting the API."
            )
        return self


def build_settings(**overrides: Any) -> Settings:
    """Build settings from explicit values only, ignoring any local ``.env`` file.

    Tests use this so that a developer's local ``.env`` cannot change assertions. The
    private ``_env_file`` keyword is part of the pydantic-settings initializer but is not
    described by its type stubs.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so that configuration is parsed and validated exactly once per process.
    Tests clear the cache via ``get_settings.cache_clear()``.
    """
    return Settings()
