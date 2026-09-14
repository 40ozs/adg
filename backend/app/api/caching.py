"""Conditional GET for derived answers, keyed by collected state rather than by a clock.

One rule, and everything here follows from it: **nothing is cached behind a duration.** An
access explanation may be reused for a week if no collector has run, and must not be reused
for one second if one has. The only correct key is the state of collection, which
:class:`app.domain.CollectionBasis` names, so that is what the validator is built from.

What this gives a client
------------------------

Each derived response carries:

* ``ETag`` — a strong validator over *(contract version, collection basis, this exact
  request)*. Strong rather than weak because the body really is byte-identical for a
  repeated request over an unchanged basis: the engine is deterministic and every listing it
  produces is deterministically ordered, which Phase 5A made a tested property.
* ``Cache-Control: private, no-cache`` — store it, and revalidate before every reuse.
  ``no-cache`` does not mean "do not cache"; it means "do not serve without asking", which
  is the exact promise this design can keep. ``private`` because the answer is a
  reconnaissance map of one estate and must not sit in a shared proxy.
* ``X-ADG-Collection-Basis`` — the basis token on its own, so an operator can see from a
  response header whether two answers came from the same collected state.

A client that returns the ``ETag`` in ``If-None-Match`` gets ``304 Not Modified`` with no
body whenever nothing has been collected since.

Why this is cheap, and what it does not do
------------------------------------------

The validator is computable **before** the expensive work: it needs one aggregate over
``scan_runs`` and the request's own parameters, and neither requires the membership
traversal, the ACL reads, or the access check. So a revalidation that hits costs one small
query rather than a full explanation — which is the case that matters, because a UI holding
an explanation open re-requests it far more often than the estate changes.

It does **not** memoize responses in the process. That was considered and rejected:

* it saves nothing a conditional GET does not already save, since the 304 path is already
  one query;
* a process-wide cache keyed by principal and resource is one forgotten key component away
  from serving one caller's answer to another, and this payload names the group to join and
  the ACE to edit to reach a share;
* and an in-process cache would have to be invalidated, which is the class of bug this
  module exists to avoid rather than to reintroduce one layer down.

If a future phase needs server-side memoization, the basis token is the key it should use,
and the caller's capability set has to be part of that key.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable, Mapping
from typing import Any, Final

from fastapi import Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.basis import CollectionBasis
from app.repositories.basis import CollectionBasisRepository

__all__ = [
    "BASIS_HEADER",
    "CACHE_CONTROL",
    "CONTRACT_VERSION",
    "BasisView",
    "CacheValidator",
    "basis_view",
    "current_validator",
    "not_modified",
    "validator_for",
]

CONTRACT_VERSION: Final = "1.0"
"""The version of the derived-answer response contract published under
``docs/contracts/v1/``.

Part of every validator, so that deploying a version of ADG that renders these responses
differently invalidates every cached copy of the old rendering. Without it a client could
hold a 1.0 body, revalidate against a 1.1 server over an unchanged estate, receive 304, and
go on rendering fields that no longer mean what it thinks. Bump it whenever the shape of a
derived response changes; ``tests/contracts`` checks it against the published schemas.
"""

CACHE_CONTROL: Final = "private, no-cache"
"""Store it, never serve it without revalidating, never in a shared cache."""

BASIS_HEADER: Final = "X-ADG-Collection-Basis"
"""The basis token alone, for operators and for logs. Never a validator on its own: two
requests with different parameters share a basis and have different answers."""


class CacheValidator:
    """The ETag for one request, and the decision to answer 304 or to compute.

    Built before the work, deliberately. See the module docstring: the whole value of this
    is that a revalidation costs one aggregate query.
    """

    __slots__ = ("_basis", "_etag")

    def __init__(self, basis: CollectionBasis, etag: str) -> None:
        self._basis = basis
        self._etag = etag

    @property
    def basis(self) -> CollectionBasis:
        return self._basis

    @property
    def etag(self) -> str:
        """The validator, quoted as HTTP requires."""
        return self._etag

    def matches(self, if_none_match: str | None) -> bool:
        """Whether the client already holds this exact answer.

        ``If-None-Match`` is a comma-separated list and may be ``*``; both are handled
        because a client library is entitled to send either. A weak comparison is used —
        ``W/"x"`` matches ``"x"`` — which is what RFC 9110 requires for ``If-None-Match``
        regardless of how the server generated the tag.
        """
        if not if_none_match:
            return False
        candidates = [value.strip() for value in if_none_match.split(",")]
        if "*" in candidates:
            return True
        return any(_weakened(value) == _weakened(self._etag) for value in candidates if value)

    def apply(self, response: Response) -> None:
        """Stamp the validator and the caching rules onto an outgoing response."""
        response.headers["ETag"] = self._etag
        response.headers["Cache-Control"] = CACHE_CONTROL
        response.headers[BASIS_HEADER] = self._basis.token
        # Two requests differing only in this header have different answers, and a cache
        # that ignored it would serve one client's revalidation result to another.
        response.headers["Vary"] = "Authorization, If-None-Match"


def validator_for(
    basis: CollectionBasis, *, route: str, parameters: Mapping[str, Any]
) -> CacheValidator:
    """Build the validator for one request.

    ``parameters`` must name **every** input that can change the body — path parameters,
    query parameters, and any default the server resolved for an omitted one. A missing
    component is not a lost optimization; it is a wrong answer served as a fresh one, which
    is why the route's own tests assert that changing each parameter changes the tag.

    ``None`` values are kept rather than dropped, so that "parameter absent" and "parameter
    present and null" are distinguishable, and so adding an optional parameter to a route
    changes the tag for requests that do not send it — which is correct, because the server
    may now resolve a different default.
    """
    material = "\x1e".join((CONTRACT_VERSION, basis.token, route, *_rendered(parameters)))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return CacheValidator(basis, f'"{CONTRACT_VERSION}-{basis.token}-{digest}"')


async def current_validator(
    session: AsyncSession, *, route: str, parameters: Mapping[str, Any]
) -> CacheValidator:
    """Read the collection basis and build this request's validator.

    The one query a derived endpoint issues before it decides whether to do any work. Every
    such endpoint calls this rather than assembling its own, so that all of them invalidate
    on the same event and none can quietly key a cache on something narrower.
    """
    basis = await CollectionBasisRepository(session).current()
    return validator_for(basis, route=route, parameters=parameters)


def not_modified(validator: CacheValidator) -> Response:
    """A bodiless 304 carrying the validator that produced it.

    The headers are repeated on a 304 because a client that receives one updates the stored
    response's headers from it; omitting ``Cache-Control`` there would let a client's copy
    fall back to a heuristic freshness lifetime, which is the one thing this module refuses
    to allow.
    """
    response = Response(status_code=status.HTTP_304_NOT_MODIFIED)
    validator.apply(response)
    return response


class BasisView(BaseModel):
    """The collected state an answer was computed from, as the contract renders it.

    Every derived response carries one. It is what lets a client say *"as of the run that
    finished at 02:14"* instead of *"as of now"*, and what lets two answers fetched minutes
    apart be compared honestly: same token, same facts, and any difference between them is a
    difference in the question rather than in the estate.
    """

    token: str = Field(
        description="The cache identity of the collected state. Equal tokens mean identical "
        "stored facts; this is the value the ETag is built from."
    )
    runs: int = Field(description="Scan runs on record.")
    latest_run_id: str | None = Field(
        default=None, description="The most recently updated run. Informational, not the key."
    )
    latest_activity_at: dt.datetime | None = Field(
        default=None, description="When a collector last wrote anything."
    )
    observations_applied: int
    batches_received: int
    is_empty: bool = Field(
        description="True when no run has ever been recorded. Every derived answer over an "
        "empty estate reports no access, and here that means nobody looked."
    )


def basis_view(basis: CollectionBasis) -> BasisView:
    """The basis as the published contract renders it."""
    return BasisView(
        token=basis.token,
        runs=basis.runs,
        latest_run_id=basis.latest_run_id,
        latest_activity_at=basis.latest_activity_at,
        observations_applied=basis.observations_applied,
        batches_received=basis.batches_received,
        is_empty=basis.is_empty,
    )


def _rendered(parameters: Mapping[str, Any]) -> Iterable[str]:
    """Parameters as stable text, sorted by name so argument order cannot change the tag."""
    for name in sorted(parameters):
        yield f"{name}={_value(parameters[name])}"


def _value(value: Any) -> str:
    if value is None:
        return "\x00"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(_value(item) for item in value)
    return str(value)


def _weakened(etag: str) -> str:
    """An entity tag with its weakness prefix removed, for the weak comparison."""
    value = etag.strip()
    return value[2:] if value.startswith("W/") else value
