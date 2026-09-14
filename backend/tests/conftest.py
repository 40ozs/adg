"""Shared test fixtures.

The API tests never touch a real PostgreSQL instance: they install a stub database on
``app.state`` so that health behavior is tested deterministically. The one test that does
require PostgreSQL is marked ``smoke`` and is opt-in.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings, build_settings
from app.db import ConnectivityResult
from app.main import create_app
from app.runtime import install_selector_event_loop_policy

# Must run before pytest-asyncio creates the first event loop: the PostgreSQL driver
# cannot use Windows' default ProactorEventLoop. See app.runtime.
install_selector_event_loop_policy()


class StubDatabase:
    """Minimal stand-in for :class:`app.db.Database` used by API tests."""

    def __init__(self, result: ConnectivityResult) -> None:
        self.result = result
        self.disposed = False

    async def check_connectivity(self) -> ConnectivityResult:
        return self.result

    async def dispose(self) -> None:
        self.disposed = True


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove ambient ADG_* variables so a developer's shell cannot change test outcomes."""
    for key in [name for name in os.environ if name.startswith("ADG_")]:
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def settings() -> Settings:
    return build_settings(
        environment="test",
        log_format="text",
        cors_allow_origins="http://localhost:3000",
    )


@pytest.fixture
def healthy_database() -> StubDatabase:
    return StubDatabase(ConnectivityResult(ok=True, latency_ms=1.5))


@pytest.fixture
def unhealthy_database() -> StubDatabase:
    return StubDatabase(
        ConnectivityResult(
            ok=False,
            latency_ms=12.0,
            error="OperationalError: connection to server at localhost failed",
        )
    )


def build_client(settings: Settings, database: object) -> AsyncClient:
    app = create_app(settings)
    app.state.database = database
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
async def healthy_client(
    settings: Settings, healthy_database: StubDatabase
) -> AsyncIterator[AsyncClient]:
    async with build_client(settings, healthy_database) as client:
        yield client


@pytest.fixture
async def unhealthy_client(
    settings: Settings, unhealthy_database: StubDatabase
) -> AsyncIterator[AsyncClient]:
    async with build_client(settings, unhealthy_database) as client:
        yield client
