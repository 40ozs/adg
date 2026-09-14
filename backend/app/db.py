"""Database connectivity abstraction.

Everything that talks to PostgreSQL goes through :class:`Database`. Keeping the engine
behind a small, explicit surface means later phases can add repositories and unit-of-work
helpers without the API layer importing SQLAlchemy directly, and lets tests substitute a
stub implementation.

This module intentionally contains no schema: tables arrive through Alembic migrations
under ``database/migrations``.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.runtime import PROACTOR_HINT


@dataclass(frozen=True, slots=True)
class ConnectivityResult:
    """Outcome of a database reachability probe.

    ``error`` is a short, actionable diagnostic suitable for a health endpoint; it never
    contains credentials because the connection string is not echoed back.
    """

    ok: bool
    latency_ms: float
    error: str | None = None


class Database:
    """Owns the async engine and session factory for one application instance."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            pool_pre_ping=True,
            connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
            future=True,
        )
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session that is rolled back on error and always closed."""
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def check_connectivity(self) -> ConnectivityResult:
        """Probe the database with ``SELECT 1``.

        Never raises: readiness reporting must be able to describe a failure rather than
        turn it into a 500.
        """
        started = time.perf_counter()
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            return ConnectivityResult(
                ok=False,
                latency_ms=_elapsed_ms(started),
                error=_diagnostic(exc),
            )
        except OSError as exc:  # DNS failure, refused connection, unreachable host
            return ConnectivityResult(
                ok=False,
                latency_ms=_elapsed_ms(started),
                error=f"{type(exc).__name__}: {exc}",
            )
        return ConnectivityResult(ok=True, latency_ms=_elapsed_ms(started))

    async def dispose(self) -> None:
        """Close pooled connections. Called on application shutdown."""
        await self._engine.dispose()


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _diagnostic(exc: SQLAlchemyError) -> str:
    """Reduce a SQLAlchemy error to one actionable line without leaking credentials."""
    cause = exc.__cause__ or exc
    message = str(cause).strip().splitlines()
    first_line = message[0] if message else type(cause).__name__
    diagnostic = f"{type(cause).__name__}: {first_line}"
    if "ProactorEventLoop" in first_line:
        # The driver's own message describes asyncio, not how to start this application.
        diagnostic = f"{diagnostic} {PROACTOR_HINT}"
    return diagnostic
