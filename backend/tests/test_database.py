"""Database abstraction behavior that does not need a live server."""

from __future__ import annotations

from app.config import Settings, build_settings
from app.db import Database


def unreachable_settings() -> Settings:
    # Port 1 is reserved and refuses connections immediately on a normal workstation.
    return build_settings(
        environment="test",
        database_url="postgresql+psycopg://adg:adg@127.0.0.1:1/adg",
        database_connect_timeout_seconds=1,
    )


async def test_connectivity_probe_reports_failure_instead_of_raising() -> None:
    database = Database(unreachable_settings())
    try:
        result = await database.check_connectivity()
    finally:
        await database.dispose()

    assert result.ok is False
    assert result.error is not None
    assert result.latency_ms >= 0


async def test_connectivity_diagnostic_does_not_leak_the_password() -> None:
    database = Database(unreachable_settings())
    try:
        result = await database.check_connectivity()
    finally:
        await database.dispose()

    assert result.error is not None
    assert "adg:adg@" not in result.error


def test_engine_uses_the_configured_url() -> None:
    database = Database(unreachable_settings())

    assert database.engine.url.host == "127.0.0.1"
    assert database.engine.url.port == 1
    assert database.engine.url.get_backend_name() == "postgresql"


def test_proactor_failure_explains_how_to_start_the_api() -> None:
    from sqlalchemy.exc import InterfaceError

    from app.db import _diagnostic

    cause = Exception("Psycopg cannot use the 'ProactorEventLoop' to run in async mode")
    error = InterfaceError("SELECT 1", None, cause)
    error.__cause__ = cause

    diagnostic = _diagnostic(error)

    assert "ProactorEventLoop" in diagnostic
    assert "python -m app" in diagnostic
