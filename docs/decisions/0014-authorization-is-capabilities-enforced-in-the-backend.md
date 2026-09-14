# ADR-0014: Authorization is a capability check in the backend, declared once per router

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 6A (`phase-06/01-frontend-shell-auth.md`)
- **Deciders:** Phase 6A implementation

## Context

Until this phase every ADG endpoint was open. That was survivable while the product was a
collector and a query surface with no user interface, and it stops being survivable the
moment there is a browser in front of it: the database is a map of every weak point in a
file estate, and the API is the only thing between that map and the network.

Three designs were available.

**Roles checked inside handlers.** The obvious one, and the one that fails quietly. Every
route is a place to forget the check, and a route added in a later phase is protected only by
whoever reviews it noticing. There is no way to ask the codebase "which endpoints are
unprotected?" except by reading all of them.

**A middleware matching paths.** Centralized, but it authorizes a *string*. A route whose
path does not match the pattern somebody wrote is silently public, and the patterns drift
from the routes independently — the classic reverse-proxy allow-list bug, moved inside the
application.

**Roles as the unit of permission.** Tempting, because roles are what a tenant assigns. But
a route written as "auditor or admin" has to be found and edited every time a role is added,
and the day somebody adds a role and misses one route is the day the model has two
definitions.

There is also a second consumer to think about. The frontend needs to know what to show. If
it reproduces the role-to-capability table to decide, there are two copies of the
authorization model, and the wrong one is always the one somebody trusts.

## Decision

**Authorization is a capability check, capabilities come from one frozen table, and every
router declares the capability it needs at the point it is included.**

1. `app/auth/roles.py` holds `Capability`, `Role`, and `ROLE_CAPABILITIES`: pure data and one
   pure function, no framework, no database. It is the only definition of the model.
2. Routes depend on *capabilities*, never on roles. `Depends(requires(Capability.X))`. Adding
   a role cannot change what an endpoint requires.
3. The dependency is attached where the router is included, in `app/api/__init__.py`, so the
   whole boundary is one readable list. A router with no entry there is unreachable rather
   than unprotected.
4. `/auth/me` returns the caller's **capability list**, not their roles, and the frontend
   does set membership against it. The frontend never holds a copy of the table.
5. A role ADG does not recognize contributes nothing and is reported back rather than
   dropped.

## Consequences

**The boundary is testable as a property, not as a list.** `tests/api/test_authorization.py`
reads the OpenAPI document — every route the application actually serves — and calls each one
without a credential, asserting 401. A route added in a later phase and left open fails the
suite whether or not anyone wrote a test for it. The same test asserts the public set is
*exactly* the documented one, so making something public is a deliberate edit to
`PUBLIC_PATHS` with a reason beside it.

**Dependency order became load-bearing, and is pinned.** FastAPI solves route-level
dependencies before a handler's own, and a handler's own in declaration order. An auth
dependency declared only as a parameter after `session: Session` opens a database session for
a request that is about to be rejected. This was a real defect in the first implementation of
the ingestion routes, found by giving the test's stub database a `session()` that raises. The
stub stays.

**The frontend cannot invent authority.** It can only hide things the backend would have
refused anyway. `visibleNavItems(["settings:write"])` returns the overview and nothing else,
because the frontend does not know that admins can do everything — it does not know what an
admin is.

**A capability can exist with no role granting it.** `remediation:execute` does, and
`RESERVED_CAPABILITIES` plus a test assert that no role reaches it. A future remediation route
can therefore be written against its real capability today and be provably unreachable.

**Adding a role means editing one table and its test twice.** The tests spell the expected
capability sets out literally rather than recomputing them from `ROLE_CAPABILITIES`, because
a test that derives its expectation from the table under test agrees with any table,
including a wrong one.
