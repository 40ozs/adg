"""Environment-based configuration for the ADG backend.

Configuration is read from the process environment (and from a local ``.env`` file
during development). No secret value is ever committed to source control; see
``.env.example`` at the repository root for the documented variables.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.auth.dev_users import DEFAULT_DEV_AUTH_USERS, DevelopmentUser, parse_development_users
from app.auth.roles import Role, parse_roles

Environment = Literal["development", "test", "production"]
LogFormat = Literal["json", "text"]
AuthMode = Literal["oidc", "development"]

# Development-only default. Production deployments must supply ADG_DATABASE_URL;
# `Settings` refuses to start in production while this value is still in place.
DEV_DATABASE_URL = "postgresql+psycopg://adg:adg_dev_password@localhost:5432/adg"

SUPPORTED_DB_SCHEME = "postgresql+psycopg"

#: Shortest collector key ADG will accept. A collector key is a bearer secret that lives in
#: a scheduled task's configuration on a file server; a guessable one is worse than none,
#: because it looks like a control.
MIN_COLLECTOR_KEY_LENGTH = 32


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

    # --- Authentication -------------------------------------------------------------
    #
    # 'oidc' is the production mode. 'development' is a local convenience that the
    # validators below refuse to combine with ADG_ENVIRONMENT=production.
    auth_mode: AuthMode = "development"

    # OIDC / Microsoft Entra ID. Every endpoint is named explicitly rather than
    # discovered: a startup that silently reaches out to the network to learn where to
    # verify signatures is a startup whose security depends on DNS.
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_client_id: str = ""
    oidc_authorization_endpoint: str = ""
    oidc_token_endpoint: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_roles_claim: str = "roles"
    oidc_groups_claim: str = "groups"
    # Comma-separated '<group object id>:<adg role>' pairs, for tenants that assign access
    # by security group rather than by app role.
    oidc_group_role_map: str = ""

    # Development mode only. The secret signs the tokens this process itself issues; when
    # it is blank a random one is generated per process, so restarting invalidates every
    # development session and no default secret exists to be reused somewhere real.
    dev_auth_secret: str = ""
    dev_auth_users: str = DEFAULT_DEV_AUTH_USERS
    dev_auth_token_lifetime_minutes: int = Field(default=480, ge=1, le=1440)

    # Comma-separated '<key id>:<secret>' pairs that collectors present as
    # X-ADG-Collector-Key. Empty means ingestion requires an administrator's bearer token.
    collector_api_keys: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development_auth(self) -> bool:
        return self.auth_mode == "development"

    @property
    def oidc_scope_list(self) -> list[str]:
        return [scope for scope in self.oidc_scopes.split() if scope]

    @property
    def resolved_dev_auth_secret(self) -> str:
        """The signing secret for development tokens, configured or generated."""
        return self.dev_auth_secret

    @property
    def development_users(self) -> tuple[DevelopmentUser, ...]:
        return parse_development_users(self.dev_auth_users)

    @property
    def group_role_map(self) -> dict[str, Role]:
        """Group object id (casefolded) -> role, as validated at startup."""
        return _parse_group_role_map(self.oidc_group_role_map)

    @property
    def collector_key_map(self) -> dict[str, str]:
        """Key id -> secret, as validated at startup. Empty when none are configured."""
        return _parse_collector_keys(self.collector_api_keys)

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

    @model_validator(mode="after")
    def _reject_development_auth_in_production(self) -> Settings:
        """The rule that makes development authentication safe to ship.

        Development mode signs its own tokens and asks for no credential. It is a
        configuration error rather than a risk decision, so it is refused at startup: the
        process does not begin, instead of beginning with an open front door.
        """
        if self.is_production and self.is_development_auth:
            raise ValueError(
                "ADG_AUTH_MODE=development cannot be used with ADG_ENVIRONMENT=production. "
                "Development authentication issues its own tokens and verifies no "
                "credential. Set ADG_AUTH_MODE=oidc and configure ADG_OIDC_ISSUER, "
                "ADG_OIDC_AUDIENCE, and ADG_OIDC_JWKS_URL."
            )
        return self

    @model_validator(mode="after")
    def _require_oidc_configuration(self) -> Settings:
        if self.auth_mode != "oidc":
            return self
        missing = [
            name
            for name, value in (
                ("ADG_OIDC_ISSUER", self.oidc_issuer),
                ("ADG_OIDC_AUDIENCE", self.oidc_audience),
                ("ADG_OIDC_JWKS_URL", self.oidc_jwks_url),
            )
            if not value.strip()
        ]
        if missing:
            raise ValueError(
                f"ADG_AUTH_MODE=oidc requires {', '.join(missing)}. For Microsoft Entra ID "
                "these are https://login.microsoftonline.com/<tenant>/v2.0, the application "
                "ID URI or client id ADG's tokens are issued for, and "
                "https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys."
            )
        for name, value in (
            ("ADG_OIDC_ISSUER", self.oidc_issuer),
            ("ADG_OIDC_JWKS_URL", self.oidc_jwks_url),
        ):
            if not value.strip().startswith("https://"):
                raise ValueError(
                    f"{name} must be an https URL; received {value!r}. Token verification "
                    "over plain http would let anything on the network mint identities."
                )
        return self

    @model_validator(mode="after")
    def _validate_auth_tables(self) -> Settings:
        """Parse everything table-shaped once, at startup, where the error is visible.

        A malformed role map discovered on the first sign-in attempt is a mystery; the same
        error at startup names the entry.
        """
        _parse_group_role_map(self.oidc_group_role_map)
        _parse_collector_keys(self.collector_api_keys)
        if self.is_development_auth:
            parse_development_users(self.dev_auth_users)
        return self

    @model_validator(mode="after")
    def _generate_development_secret(self) -> Settings:
        """Give development mode a signing secret when none was configured.

        Generated per process rather than defaulted to a constant: a constant in the
        repository is a secret in the repository, and somebody would eventually verify a
        token with it somewhere that mattered.
        """
        if self.is_development_auth and not self.dev_auth_secret.strip():
            self.dev_auth_secret = secrets.token_urlsafe(48)
        return self


def _parse_group_role_map(raw: str) -> dict[str, Role]:
    mapping: dict[str, Role] = {}
    for entry in raw.split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        group, separator, role_name = candidate.rpartition(":")
        if not separator or not group.strip():
            raise ValueError(
                f"ADG_OIDC_GROUP_ROLE_MAP entry {candidate!r} is not '<group object id>:<role>'."
            )
        roles, unknown = parse_roles([role_name])
        if unknown or not roles:
            raise ValueError(
                f"ADG_OIDC_GROUP_ROLE_MAP entry {candidate!r} names unknown role "
                f"{role_name.strip()!r}. Valid roles: "
                f"{', '.join(sorted(role.value for role in Role))}."
            )
        mapping[group.strip().casefold()] = next(iter(roles))
    return mapping


def _parse_collector_keys(raw: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for entry in raw.split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        key_id, separator, secret = candidate.partition(":")
        if not separator or not key_id.strip() or not secret.strip():
            raise ValueError(
                f"ADG_COLLECTOR_API_KEYS entry {key_id.strip()!r} is not '<key id>:<secret>'."
            )
        if key_id.strip() in keys:
            raise ValueError(
                f"ADG_COLLECTOR_API_KEYS names key id {key_id.strip()!r} more than once."
            )
        if len(secret.strip()) < MIN_COLLECTOR_KEY_LENGTH:
            raise ValueError(
                f"The collector key {key_id.strip()!r} is shorter than "
                f"{MIN_COLLECTOR_KEY_LENGTH} characters. Generate one with "
                "[Convert]::ToBase64String((1..32 | ForEach-Object "
                "{ Get-Random -Maximum 256 }))."
            )
        keys[key_id.strip()] = secret.strip()
    return keys


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
