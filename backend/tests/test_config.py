"""Configuration validation.

Configuration errors must be loud and actionable at startup rather than surfacing later as
a confusing runtime failure.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import DEV_DATABASE_URL, MIN_SIGNING_KEY_LENGTH, build_settings


def test_defaults_are_development_oriented() -> None:
    settings = build_settings()

    assert settings.app_name == "ADG"
    assert settings.environment == "development"
    assert settings.log_format == "json"
    assert settings.database_url == DEV_DATABASE_URL


def test_log_level_is_normalized_to_upper_case() -> None:
    assert build_settings(log_level="debug").log_level == "DEBUG"


def test_unknown_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="ADG_LOG_LEVEL"):
        build_settings(log_level="chatty")


def test_non_postgresql_database_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="postgresql\\+psycopg"):
        build_settings(database_url="sqlite:///adg.db")


def test_production_refuses_the_development_database_default() -> None:
    with pytest.raises(ValidationError, match="development default"):
        build_settings(environment="production")


def test_production_starts_with_an_explicit_database_url() -> None:
    """Phase 6A added a second production requirement: real authentication.

    The database URL alone is no longer enough to start in production, so this test now
    supplies the OIDC configuration too. That is the point of the rule rather than an
    inconvenience of it: a production process that could start without it would be a
    production process serving the estate to anyone who asked.
    """
    settings = build_settings(
        environment="production",
        database_url="postgresql+psycopg://adg:secret@db.internal:5432/adg",
        auth_mode="oidc",
        oidc_issuer="https://login.microsoftonline.com/tenant/v2.0",
        oidc_audience="api://adg",
        oidc_jwks_url="https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
    )

    assert settings.is_production is True
    assert settings.is_development_auth is False


def test_cors_origins_are_split_and_trimmed() -> None:
    settings = build_settings(cors_allow_origins="http://localhost:3000, https://adg.example.com ,")

    assert settings.cors_origin_list == ["http://localhost:3000", "https://adg.example.com"]


def test_connect_timeout_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        build_settings(database_connect_timeout_seconds=0)


class TestTheChangePlanSigningKey:
    """Three states, and only the middle one is a mistake.

    Empty means this deployment cannot export, which the export path refuses in full. A long
    key signs. A short key is the case the release audit added: it signs *and* is guessable,
    so every document it produces verifies and none of them proves anything — a control that
    reads as one from every side except the one that matters.
    """

    def test_no_key_is_allowed_because_it_means_export_is_disabled(self) -> None:
        assert build_settings().remediation_signing_key == ""

    def test_a_long_key_is_accepted(self) -> None:
        key = "s" * MIN_SIGNING_KEY_LENGTH
        assert build_settings(remediation_signing_key=key).remediation_signing_key == key

    def test_a_short_key_is_refused_at_startup(self) -> None:
        with pytest.raises(ValidationError, match="ADG_REMEDIATION_SIGNING_KEY"):
            build_settings(remediation_signing_key="s" * (MIN_SIGNING_KEY_LENGTH - 1))

    def test_whitespace_does_not_make_a_short_key_long(self) -> None:
        """Measured after stripping, because the signer strips before it uses the value."""
        with pytest.raises(ValidationError, match="ADG_REMEDIATION_SIGNING_KEY"):
            build_settings(remediation_signing_key="  short  ")

    def test_the_message_names_the_setting_and_how_to_generate_one(self) -> None:
        with pytest.raises(ValidationError) as caught:
            build_settings(remediation_signing_key="too-short")

        message = str(caught.value)
        assert "ADG_REMEDIATION_SIGNING_KEY" in message
        assert str(MIN_SIGNING_KEY_LENGTH) in message
        assert "Get-Random" in message
