"""Fixtures for the tests that need a real PostgreSQL.

These are marked ``smoke`` and skipped unless ``ADG_RUN_SMOKE_TESTS=1``, matching the
convention the bootstrap phase established. They run against a **separate database**
(``<dev database>_test``), created and migrated once per session, so a developer's local
data is never truncated by a test run.

The schema is built by running ``alembic upgrade head`` as a subprocess, deliberately: that
is what an operator runs, so the migration itself is under test rather than a convenient
``metadata.create_all`` that would pass even if the revision were wrong.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import Role
from app.config import Settings, build_settings
from app.db import Database
from app.main import create_app
from app.models.schema import metadata
from tests.support.auth import auth_headers, token_for_roles

SMOKE_ENABLED = os.getenv("ADG_RUN_SMOKE_TESTS") == "1"
# Read at import time, and through Settings rather than os.environ alone: the development
# credentials live in the repository's .env, and the autouse fixture in the root conftest
# strips every ADG_* variable before each test runs.
BASE_DATABASE_URL = os.getenv("ADG_DATABASE_URL") or Settings().database_url
BACKEND_ROOT = Path(__file__).resolve().parents[2]

SKIP_REASON = (
    "Set ADG_RUN_SMOKE_TESTS=1 with PostgreSQL running, or run scripts/backend-test.ps1 -Smoke."
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark everything in this package ``smoke``, and skip it without a database.

    Applied as a hook rather than a module-level ``pytestmark`` because a conftest's
    ``pytestmark`` does not reach the test modules beside it — relying on that would let
    these run, and truncate tables, during an ordinary hermetic test run.
    """
    here = Path(__file__).parent
    for item in items:
        if here not in Path(str(item.fspath)).parents:
            continue
        item.add_marker(pytest.mark.smoke)
        if not SMOKE_ENABLED:
            item.add_marker(pytest.mark.skip(reason=SKIP_REASON))


def _render(url: sa.engine.URL) -> str:
    """A connectable URL string.

    ``str(URL)`` replaces the password with ``***`` — deliberately, so a URL cannot be
    logged with credentials in it. Rendering one to connect with therefore has to opt out
    explicitly, or every connection fails authentication for no visible reason.
    """
    return url.render_as_string(hide_password=False)


def _test_database_url() -> str:
    """The development URL with ``_test`` appended to the database name."""
    url = sa.engine.make_url(BASE_DATABASE_URL)
    return _render(url.set(database=f"{url.database}_test"))


TEST_DATABASE_URL = _test_database_url()


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Create the test database and bring it to head. Returns its URL."""
    url = sa.engine.make_url(TEST_DATABASE_URL)
    admin = sa.create_engine(
        _render(url.set(database="postgres", drivername="postgresql+psycopg")),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
            ).scalar_one_or_none()
            if exists is None:
                connection.execute(sa.text(f'CREATE DATABASE "{url.database}"'))
    finally:
        admin.dispose()

    environment = {**os.environ, "ADG_DATABASE_URL": TEST_DATABASE_URL}
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "alembic upgrade head failed against the test database:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return TEST_DATABASE_URL


@pytest.fixture
def db_settings(migrated_database: str) -> Settings:
    return build_settings(environment="test", log_format="text", database_url=migrated_database)


@pytest.fixture
async def database(db_settings: Settings) -> AsyncIterator[Database]:
    instance = Database(db_settings)
    try:
        yield instance
    finally:
        await instance.dispose()


@pytest.fixture(autouse=True)
def clean_tables(migrated_database: str) -> Iterator[None]:
    """Empty every table before each test.

    One TRUNCATE of all tables at once, so foreign keys never order the statements and a
    test cannot start from a partially cleaned database.
    """
    engine = sa.create_engine(migrated_database, isolation_level="AUTOCOMMIT")
    names = ", ".join(f'"{table.name}"' for table in metadata.sorted_tables)
    try:
        with engine.connect() as connection:
            connection.execute(sa.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    finally:
        engine.dispose()
    yield


@pytest.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    async with database.session() as active:
        yield active


@pytest.fixture
async def client(db_settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the real test database, signed in as an administrator.

    ``app.state.database`` is set directly rather than through the lifespan, which is the
    same seam the health-endpoint tests already use.

    The token is real: it is signed with this settings object's development secret and
    verified by the application's own verifier on every request. Nothing here overrides the
    authentication dependency, so these suites exercise the application as deployed rather
    than an application with its front door removed. ``tests/db/test_authorization.py``
    covers what happens without a token and with an insufficient one.
    """
    app = create_app(db_settings)
    app.state.database = database
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers=auth_headers(db_settings),
    ) as active:
        yield active


@pytest.fixture
async def anonymous_client(db_settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
    """The same application, with no credential at all."""
    app = create_app(db_settings)
    app.state.database = database
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as active:
        yield active


@pytest.fixture
def client_as(
    db_settings: Settings, database: Database
) -> Callable[..., AbstractAsyncContextManager[AsyncClient]]:
    """A factory for clients holding exactly the roles named — including none."""

    @asynccontextmanager
    async def build(*roles: Role) -> AsyncIterator[AsyncClient]:
        app = create_app(db_settings)
        app.state.database = database
        token = token_for_roles(db_settings, *roles)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}"},
        ) as active:
            yield active

    return build
