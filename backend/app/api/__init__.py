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

from app.api import (
    access,
    alerts,
    auth,
    changes,
    collection,
    governance,
    graph,
    groups,
    health,
    meta,
    remediation,
    resources,
    risks,
    scan_runs,
    search,
    simulations,
)
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
    # A group's resource impact is an access answer about many resources at once, so it
    # requires ACCESS_READ and not the IDENTITIES_READ that the rest of /api/v1/groups
    # carries. Knowing who is in a group and knowing what that group reaches are different
    # disclosures, and the more sensitive one does not inherit the weaker requirement.
    api_router.include_router(
        groups.router, dependencies=[Depends(requires(Capability.ACCESS_READ))]
    )
    # The change feed, the summary, one object's timeline, and the point-in-time
    # comparison: all of them report what was edited.
    api_router.include_router(
        changes.router, dependencies=[Depends(requires(Capability.CHANGES_READ))]
    )
    # "Why did access change" is an access answer and requires ACCESS_READ, not the
    # CHANGES_READ the rest of /api/v1/changes carries. Knowing that an ACE was added and
    # knowing what a principal could consequently do are different disclosures, and the more
    # sensitive one does not inherit the weaker requirement -- the same line already drawn
    # between /api/v1/groups' membership routes and its resource-impact route.
    api_router.include_router(
        changes.impact_router, dependencies=[Depends(requires(Capability.ACCESS_READ))]
    )
    # Search spans areas, so it requires only the capability to search and then filters
    # each category by the caller's own capabilities; see app.services.search.
    api_router.include_router(search.router, dependencies=[Depends(requires(Capability.SEARCH))])

    # The risk report. One capability, and no route here can start an evaluation: reading
    # a report must not be able to trigger the most expensive thing in the product.
    api_router.include_router(risks.router, dependencies=[Depends(requires(Capability.RISKS_READ))])
    # Alerts, watches and the delivery queue. Two capabilities divide them and the router
    # declares each at its own route -- the same shape governance and scan_runs use -- so it
    # is included without a blanket dependency here. Reading an alert and deciding who gets
    # woken up are separately held: somebody who could quietly disable the watch on the
    # payroll share could make an exposure land in nobody's inbox.
    api_router.include_router(alerts.router)

    # Governance: ADG's own records *about* the collected facts -- owners, review
    # campaigns, attestations, proposed remediation, and the audit trail. Three capabilities
    # divide it and the router declares each at its own route, so it is included without a
    # blanket dependency here -- the same shape scan_runs uses. Reading, answering and
    # running a review are separately held: see app/auth/roles.py and ADR-0029.
    api_router.include_router(governance.router)

    # What-if proposals. Two capabilities divide them and the router declares each at its
    # own route, so it is included without a blanket dependency here. Reading a proposal
    # somebody else ran and computing a new one are separately held: a simulation discloses
    # *potential* access, which is a route map for privilege escalation, and running one is
    # the most expensive request this API serves. Nothing here writes to Windows -- see
    # ADR-0034 and the module docstring.
    api_router.include_router(simulations.router)

    # Change plans. Four capabilities divide them and the router declares each at its own
    # route, so it is included without a blanket dependency here. **No route in it writes to
    # Windows**: ADG has no write adapter, remediation:execute is granted by no role, and
    # nothing reaches an executor except the execution-policy route, which asks the executor
    # to describe itself. Proposing, approving and exporting are held by disjoint roles --
    # see app/auth/roles.py, ADR-0035 and ADR-0038.
    api_router.include_router(remediation.router)

    # Collector operations. The ingestion routes carry their own dependency, because a
    # collector key rather than a role is the normal credential there.
    api_router.include_router(scan_runs.router)
    api_router.include_router(collection.router)

    return api_router


__all__ = ["PUBLIC_PATHS", "build_api_router"]
