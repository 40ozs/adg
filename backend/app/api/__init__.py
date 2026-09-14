"""HTTP API routers, and the one place the authorization boundary is declared.

``build_api_router`` is the single aggregation point; later phases attach their routers here
so that the application factory stays stable. It is a function rather than a module-level
router because two things depend on configuration: the development login route exists only
in development mode, and each application built in a test session needs its own routes.

Health and version endpoints sit at the root because probes and humans read them. Everything
that speaks about collected facts lives under ``/api/v1``, which is the version boundary the
collector contract is pinned to: a breaking change means ``/api/v2``, never an edit here.

**Every data router is included with a capability dependency, here, in one visible list.**
Enforcement lives at the include site rather than inside each handler so that adding a route
cannot accidentally add an unprotected one — a router with no entry in this file is a router
nothing can reach. ``tests/api/test_authorization.py`` asserts the list is exhaustive against
the OpenAPI document, so a new unprotected path fails the suite rather than shipping.

Deliberately public:

* ``/health/live`` and ``/health/ready`` — read by orchestrators that hold no token, and
  they disclose only reachability and a version.
* ``/version`` — the same.
* ``/auth/config`` — a client cannot authenticate before it knows how to.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api import access, auth, collection, graph, health, meta, resources, scan_runs, search
from app.auth.dependencies import requires
from app.auth.roles import Capability
from app.config import Settings

#: Paths that answer without a token, and why each one has to. ``/auth/dev/login`` is on
#: the list because it *is* the way a development session is obtained — and it exists only
#: when ``ADG_AUTH_MODE=development``, which cannot be combined with production.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {"/health/live", "/health/ready", "/version", "/auth/config", "/auth/dev/login"}
)


def build_api_router(settings: Settings) -> APIRouter:
    """Every route this deployment serves, each behind the capability it requires."""
    api_router = APIRouter()

    # Unauthenticated by design; see PUBLIC_PATHS.
    api_router.include_router(health.router)
    api_router.include_router(meta.router)

    # Authentication: /auth/config is public, /auth/me requires only a valid token, and
    # /auth/dev/login exists only in development mode.
    api_router.include_router(auth.build_auth_router(settings))

    # Collected facts. One capability per area.
    api_router.include_router(
        graph.router, dependencies=[Depends(requires(Capability.IDENTITIES_READ))]
    )
    api_router.include_router(
        resources.router, dependencies=[Depends(requires(Capability.RESOURCES_READ))]
    )
    api_router.include_router(
        access.router, dependencies=[Depends(requires(Capability.ACCESS_READ))]
    )
    # Search spans areas, so it requires only the capability to search and then filters
    # each category by the caller's own capabilities; see app.services.search.
    api_router.include_router(search.router, dependencies=[Depends(requires(Capability.SEARCH))])

    # Collector operations. The ingestion routes carry their own dependency, because a
    # collector key rather than a role is the normal credential there.
    api_router.include_router(scan_runs.router)
    api_router.include_router(collection.router)

    return api_router


__all__ = ["PUBLIC_PATHS", "build_api_router"]
