"""The identity behind one request, and how token claims become one.

``AuthenticatedPrincipal`` is the only thing the API knows about a caller. It is built once
per request from verified token claims and is immutable afterwards, so nothing downstream
can widen its own authority.

The claims-to-principal mapping lives here as a pure function. It is the part most likely
to be wrong against a real tenant — role claims arrive under different names, as a string
or a list, as app roles or as group object ids — and a pure function is the part that can
be tested exhaustively without a tenant.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from app.auth.roles import RESERVED_ROLES, Capability, Role, capabilities_for, parse_roles

__all__ = [
    "AuthSource",
    "AuthenticatedPrincipal",
    "ClaimsMapping",
    "collector_principal",
    "principal_from_claims",
]

AuthSource = Literal["oidc", "development", "collector_key"]


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Who is making this request, and what they may do.

    ``granted`` is derived from ``roles`` at construction and is what every authorization
    check reads. ``reserved_roles`` and ``unknown_roles`` carry nothing operative; they
    exist so that ``/auth/me`` and the logs can explain *why* a caller has less authority
    than the administrator expected.
    """

    subject: str
    source: AuthSource
    roles: frozenset[Role] = frozenset()
    granted: frozenset[Capability] = frozenset()
    display_name: str | None = None
    email: str | None = None
    reserved_roles: frozenset[Role] = frozenset()
    unknown_roles: tuple[str, ...] = ()
    issuer: str | None = None
    expires_at: dt.datetime | None = None
    #: Set on a principal whose credential is a development credential, so that every
    #: surface that shows an identity can say so without re-deriving it from the mode.
    development: bool = field(default=False)

    def can(self, capability: Capability) -> bool:
        return capability in self.granted

    @property
    def is_authorized_for_anything(self) -> bool:
        """Whether this principal holds any capability at all.

        A token that authenticates but grants nothing is a configuration error worth
        distinguishing from a token that is merely short of one capability.
        """
        return bool(self.granted)


@dataclass(frozen=True, slots=True)
class ClaimsMapping:
    """Where in a token's claims the roles are, and how tenant values map onto ADG roles.

    Entra ID can deliver authorization in two shapes. App role assignments arrive in
    ``roles`` as the *value* strings configured on the app registration; group assignments
    arrive in ``groups`` as object ids, which mean nothing to ADG until an operator maps
    them. Both are supported, and both funnel through :func:`parse_roles`.
    """

    roles_claim: str = "roles"
    groups_claim: str = "groups"
    #: Group object id (casefolded) -> ADG role.
    group_roles: Mapping[str, Role] = field(default_factory=dict)

    def resolve(self, claims: Mapping[str, Any]) -> tuple[list[str], list[str]]:
        """Raw role strings this token carries, plus the group ids that mapped to none."""
        raw = list(_string_list(claims.get(self.roles_claim)))
        unmapped: list[str] = []
        for group in _string_list(claims.get(self.groups_claim)):
            mapped = self.group_roles.get(group.strip().casefold())
            if mapped is None:
                unmapped.append(group)
            else:
                raw.append(mapped.value)
        return raw, unmapped


def principal_from_claims(
    claims: Mapping[str, Any],
    *,
    mapping: ClaimsMapping,
    source: AuthSource,
) -> AuthenticatedPrincipal:
    """Build a principal from claims that have **already been cryptographically verified**.

    This function trusts its input: it performs no signature, issuer, audience, or expiry
    check. Those belong to :mod:`app.auth.tokens`, which is the only caller.
    """
    subject = _first_string(claims, "sub", "oid")
    if not subject:
        raise ValueError(
            "The token carries no 'sub' claim, so there is no identity to authorize. "
            "Check that the application is requesting an ID/access token and not an "
            "opaque token."
        )

    raw_roles, unmapped_groups = mapping.resolve(claims)
    roles, unknown = parse_roles(raw_roles)
    # Unmapped group ids are reported alongside unknown role strings: from an operator's
    # point of view they are the same mistake — an assignment ADG could not act on.
    unknown = tuple(dict.fromkeys(unknown + tuple(f"group:{value}" for value in unmapped_groups)))

    return AuthenticatedPrincipal(
        subject=subject,
        source=source,
        roles=roles,
        granted=capabilities_for(roles),
        display_name=_first_string(claims, "name", "preferred_username"),
        email=_first_string(claims, "email", "upn", "preferred_username"),
        reserved_roles=roles & RESERVED_ROLES,
        unknown_roles=unknown,
        issuer=_first_string(claims, "iss"),
        expires_at=_expiry(claims.get("exp")),
        development=source == "development",
    )


def collector_principal(key_id: str) -> AuthenticatedPrincipal:
    """The principal behind a valid collector API key.

    It holds exactly one capability. A collector key is a machine credential for writing
    observations; it must never be usable to read the estate back out, because the key
    lives in a scheduled task's configuration on a file server.
    """
    return AuthenticatedPrincipal(
        subject=f"collector:{key_id}",
        source="collector_key",
        roles=frozenset(),
        granted=frozenset({Capability.COLLECTORS_INGEST}),
        display_name=f"Collector key {key_id}",
    )


def _string_list(value: Any) -> tuple[str, ...]:
    """Claims that may be a single string or a list of them, normalized to a tuple."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _first_string(claims: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _expiry(value: Any) -> dt.datetime | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return dt.datetime.fromtimestamp(float(value), tz=dt.UTC)
