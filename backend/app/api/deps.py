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

from app.db import Database
from app.domain import DEFAULT_LIMITS, DomainValidationError, TraversalLimits
from app.repositories import MembershipRepository

__all__ = [
    "Session",
    "TraversalBounds",
    "get_session",
    "membership_repository",
    "traversal_limits",
]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    async with database.session() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


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
