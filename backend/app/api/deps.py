"""Shared request dependencies.

One database session per request, closed when the response is sent. The session comes from
``app.state.database`` rather than a module-level engine so that tests can substitute one,
which is the same seam ``/health/ready`` already uses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import Database
from app.domain import DEFAULT_LIMITS, DomainValidationError, TraversalLimits
from app.repositories import MembershipRepository

__all__ = [
    "PRINTABLE_IDENTIFIER",
    "RequestSettings",
    "Session",
    "TraversalBounds",
    "get_session",
    "membership_repository",
    "request_settings",
    "traversal_limits",
]

#: Every path parameter in this API is an identifier — a SID, a storage key, a host name, or
#: a UNC path — and none of them can contain a control character. Rejecting one here refuses
#: the request before it costs a database round trip, which matters for one value in
#: particular: a NUL byte survived every parser, reached psycopg, and raised
#: ``PostgreSQL text fields cannot contain NUL (0x00) bytes`` — a 500 for a malformed URL.
#: Phase 6D found it by fuzzing every path parameter of every route.
#:
#: Anchored with ``$``, not ``\Z``. Pydantic v2 compiles this with the Rust ``regex`` crate,
#: where ``$`` anchors at the end of the haystack — unlike Python's ``re``, where it also
#: matches before a trailing newline — so a value ending in ``%0A`` is refused. ``\Z`` would
#: say that unambiguously and is not a sequence that engine accepts.
PRINTABLE_IDENTIFIER = r"^[^\x00-\x1f\x7f]+$"


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    async with database.session() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


def request_settings(request: Request) -> Settings:
    """The settings **this application** was built with.

    Not :func:`app.config.get_settings`, which is a process-wide ``lru_cache`` over the
    environment. The two agree for a server started from the environment and diverge for every
    application built with explicit settings — which is what ``create_app(settings)`` is for,
    and what every test that needs a particular configuration does.

    The divergence is silent and was found the expensive way: a route reading ``get_settings``
    inside an app built with a signing key reported that the deployment had none, and the
    export it refused looked like a bug in the export rather than in the wiring. This is the
    same seam ``get_session`` uses, for the same reason.
    """
    settings: Settings = request.app.state.settings
    return settings


RequestSettings = Annotated[Settings, Depends(request_settings)]


def traversal_limits(
    max_depth: Annotated[
        int | None,
        Query(
            ge=1,
            description=(
                "Maximum membership hops to follow. Clamped to the server ceiling; the "
                "response reports the limits actually used."
            ),
        ),
    ] = None,
    max_nodes: Annotated[
        int | None, Query(ge=1, description="Maximum principals to enumerate.")
    ] = None,
    max_edges: Annotated[
        int | None, Query(ge=1, description="Maximum membership edges to read.")
    ] = None,
    max_paths: Annotated[
        int | None, Query(ge=1, description="Maximum distinct paths to enumerate.")
    ] = None,
) -> TraversalLimits:
    """Caller-supplied traversal bounds, clamped to the server's ceilings.

    Clamped rather than rejected: a caller asking for more than the ceiling gets the
    ceiling and is told what it got, which is more useful than a 422 for a request that
    the server can answer safely.
    """
    try:
        return DEFAULT_LIMITS.clamped(
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_edges=max_edges,
            max_paths=max_paths,
        )
    except DomainValidationError as exc:  # pragma: no cover - Query(ge=1) catches this first
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


TraversalBounds = Annotated[TraversalLimits, Depends(traversal_limits)]


def membership_repository(session: Session, limits: TraversalBounds) -> MembershipRepository:
    """A repository whose row ceiling matches the traversal's own edge budget.

    Pairing them means the database never materializes more edges than the traversal is
    allowed to consider, so a group with a million members costs a bounded query rather
    than a million rows fetched and then discarded.
    """
    return MembershipRepository(session, edge_fetch_limit=limits.max_edges + 1)
