"""Authentication and authorization for the ADG API.

The boundary is here and nowhere else. Routes declare a :class:`~app.auth.roles.Capability`
through :func:`app.auth.dependencies.requires`; the role table in :mod:`app.auth.roles`
decides who holds it; :mod:`app.auth.tokens` decides whose claim to a role is believable.

Only the framework-free halves are re-exported here. :mod:`app.auth.dependencies` and
:mod:`app.auth.tokens` are imported from their own modules, because :mod:`app.config`
validates the role tables at startup and importing them from this package would close an
import cycle through it.
"""

from __future__ import annotations

from app.auth.principal import AuthenticatedPrincipal, ClaimsMapping, principal_from_claims
from app.auth.roles import (
    ACTIVE_ROLES,
    RESERVED_CAPABILITIES,
    RESERVED_ROLES,
    ROLE_CAPABILITIES,
    Capability,
    Role,
    capabilities_for,
    parse_roles,
)

__all__ = [
    "ACTIVE_ROLES",
    "RESERVED_CAPABILITIES",
    "RESERVED_ROLES",
    "ROLE_CAPABILITIES",
    "AuthenticatedPrincipal",
    "Capability",
    "ClaimsMapping",
    "Role",
    "capabilities_for",
    "parse_roles",
    "principal_from_claims",
]
