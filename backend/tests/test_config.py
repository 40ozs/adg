"""Configuration validation.

Configuration errors must be loud and actionable at startup rather than surfacing later as
a confusing runtime failure.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import DEV_DATABASE_URL, build_settings


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
    settings = build_settings(
        environment="production",
        database_url="postgresql+psycopg://adg:secret@db.internal:5432/adg",
    )

    assert settings.is_production is True


def test_cors_origins_are_split_and_trimmed() -> None:
    settings = build_settings(cors_allow_origins="http://localhost:3000, https://adg.example.com ,")

    assert settings.cors_origin_list == ["http://localhost:3000", "https://adg.example.com"]


def test_connect_timeout_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        build_settings(database_connect_timeout_seconds=0)
